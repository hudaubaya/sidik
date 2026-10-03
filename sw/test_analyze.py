# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks for sw/analyze.py.

- reconstruct_recorded() makes the same decisions as model/ro_puf.reconstruct()
  when both see the same votes (the model's race() is replaced by recorded
  rounds);
- hand-made error patterns: majority, SECDED correction, re-measurement,
  KCV catching a miscorrection, failure, running out of races;
- CSV loading (metadata, appended files, incomplete sweeps, timeouts,
  duplicates) and the metrics on model-simulated CSVs (labelled "sim");
- --export-run output loads in fpga/char/analyze.py.
"""

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np

import analyze as swa
import ro_puf as rp
import secded

ROOT = Path(__file__).resolve().parents[1]
KP = rp.KeyGenParams()
HEADER = "board,temp_c,vdd_v,rep,pair,count_a,count_b,status"


def helper_for(key_bits):
    syn = secded.syndrome(key_bits.reshape(KP.n_blocks, secded.N))
    kcv = rp.key_check_value(rp.derive_key(key_bits), KP.kcv_bits)
    return rp.HelperData(np.arange(key_bits.size), syn, kcv)


def rounds_from(key_bits, flips_per_round):
    """Rounds of 3 votes; flips_per_round[r] = list of (vote, bit) to flip."""
    rows = []
    for flips in flips_per_round:
        v = np.tile(key_bits, (KP.n_votes, 1))
        for vote, bit in flips:
            v[vote, bit] ^= True
        rows.append(v)
    return np.vstack(rows)


def both_votes(bits):
    """Flip `bits` in votes 0 and 1, so the majority is wrong there."""
    return [(v, b) for b in bits for v in (0, 1)]


class ScenarioTest(unittest.TestCase):
    def setUp(self):
        self.key = np.random.default_rng(5).random(216) < 0.5
        self.helper = helper_for(self.key)

    def run_stream(self, flips_per_round):
        s = swa.RaceStream(rounds_from(self.key, flips_per_round), KP.n_votes)
        return swa.reconstruct_recorded(s, self.helper, KP)

    def test_clean(self):
        r = self.run_stream([[]])
        self.assertFalse(r.failed)
        self.assertEqual(r.attempts, 1)
        self.assertTrue((r.key_bits == self.key).all())

    def test_single_vote_errors_outvoted(self):
        r = self.run_stream([[(0, 3), (1, 100), (2, 200)]])
        self.assertEqual((r.failed, r.attempts), (False, 1))
        self.assertTrue((r.key_bits == self.key).all())

    def test_one_majority_error_corrected(self):
        r = self.run_stream([both_votes([80])])
        self.assertEqual((r.failed, r.attempts, r.kcv_caught), (False, 1, False))
        self.assertTrue((r.key_bits == self.key).all())

    def test_two_errors_detected_then_remeasured(self):
        r = self.run_stream([both_votes([75, 90]), []])
        self.assertEqual((r.failed, r.attempts), (False, 2))
        self.assertTrue((r.key_bits == self.key).all())

    def test_persistent_two_errors_fail(self):
        r = self.run_stream([both_votes([75, 90])] * (1 + KP.max_remeasure))
        self.assertTrue(r.failed)
        self.assertEqual(r.attempts, 1 + KP.max_remeasure)

    def test_miscorrection_caught_by_kcv(self):
        # Columns 0x80|1 ^ 0x80|2 ^ 0x80|4 = 0x80|7: SECDED "corrects" bit 7.
        r = self.run_stream([both_votes([1, 2, 4]), []])
        self.assertTrue(r.kcv_caught)
        self.assertEqual((r.failed, r.attempts), (False, 2))
        self.assertTrue((r.key_bits == self.key).all())

    def test_runs_out_of_races(self):
        self.assertIsNone(self.run_stream([both_votes([75, 90])]))
        s = swa.RaceStream(rounds_from(self.key, [[]])[:2], KP.n_votes)
        self.assertIsNone(swa.reconstruct_recorded(s, self.helper, KP))


class ModelEquivalenceTest(unittest.TestCase):
    """Same votes in, same decisions out as model/ro_puf.reconstruct()."""

    def test_random_noisy_streams(self):
        rng = np.random.default_rng(11)
        key = rng.random(216) < 0.5
        helper = helper_for(key)

        class FakeChip:
            params = rp.PufParams()

            def pair_freqs(self, temps, pairs):
                # fa carries the column index so the fake race knows which
                # key bits are being re-measured.
                return np.arange(216.0)[None, :], np.zeros((1, 216))

        n_checked = {"remeasure": 0, "failed": 0, "caught": 0}
        for trial in range(300):
            ber = rng.choice([0.002, 0.01, 0.03, 0.06])
            stream = (rng.random((3 * (1 + KP.max_remeasure), 216)) < ber) ^ key
            rounds = iter(stream.reshape(-1, KP.n_votes, 216))

            def fake_majority(fa, fb, n_votes, params, rng_):
                votes = next(rounds)[:, fa.astype(int)].transpose(1, 0, 2)
                return votes, votes.sum(axis=1) > n_votes // 2

            with mock.patch.object(rp, "_majority", fake_majority):
                want = rp.reconstruct(FakeChip(), helper, [25.0], KP, rng)
            got = swa.reconstruct_recorded(swa.RaceStream(stream, KP.n_votes), helper, KP)
            msg = f"trial {trial}"
            self.assertEqual(got.failed, bool(want.failed[0]), msg)
            self.assertEqual(got.attempts, int(want.attempts[0]), msg)
            self.assertEqual(got.kcv_caught, bool(want.kcv_caught[0]), msg)
            self.assertTrue((got.key_bits == want.key_bits[0]).all(), msg)
            self.assertTrue((got.first_votes == want.first_votes[0]).all(), msg)
            n_checked["remeasure"] += got.attempts > 1
            n_checked["failed"] += got.failed
            n_checked["caught"] += got.kcv_caught
        # The trials must exercise every branch.
        self.assertTrue(all(v > 0 for v in n_checked.values()), n_checked)


def write_csv(path, rows, meta=None, header=HEADER):
    meta = {"source": "sim", "log2n": "14", **(meta or {})}
    with open(path, "w") as fh:
        for k, v in meta.items():
            fh.write(f"# {k}={v}\n")
        fh.write(header + "\n")
        for r in rows:
            fh.write(",".join(str(x) for x in r) + "\n")


def counts_from_delta(d, n=1 << 14):
    """Counter values consistent with delta (winner reads n)."""
    return (n, n - d) if d >= 0 else (n + d, n)


def sim_csvs(directory, boards=4, temps=(-40.0, 25.0, 85.0), reps=31, seed=7):
    """Model-simulated races in measure_pairs.tcl format; one file per board
    and temperature, board 0 at 25 °C split over two files."""
    p = rp.PufParams()
    rng = np.random.default_rng(seed)
    chips = [rp.Chip(p, rng) for _ in range(boards)]
    files, deltas = [], np.zeros((boards, len(temps), reps, p.n_pairs), dtype=np.int64)
    for b, chip in enumerate(chips):
        for k, t in enumerate(temps):
            fa, fb = chip.pair_freqs(t)
            d = rp.race(fa, fb, p, rng, shape=(reps, p.n_pairs))
            deltas[b, k] = d
            parts = [(0, reps)] if (b, t) != (0, 25.0) else [(0, 10), (10, reps)]
            for i, (lo, hi) in enumerate(parts):
                rows = []
                for r in range(lo, hi):
                    for pair in range(p.n_pairs):
                        ca, cb = counts_from_delta(int(d[r, pair]))
                        rows.append((b, f"{t:g}", "nan", r - lo, pair, ca, cb, 2))
                path = Path(directory) / f"b{b}_{t:g}_{i}.csv"
                write_csv(path, rows, {"n_pairs": p.n_pairs})
                files.append(path)
    return files, deltas


class SimulatedCsvTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        files, cls.deltas = sim_csvs(cls.tmp.name)
        cls.data = swa.load(files)
        cls.res = swa.analyze(cls.data)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_loaded_exactly(self):
        self.assertEqual(self.data.boards, ["0", "1", "2", "3"])
        self.assertTrue((self.data.delta == self.deltas).all())
        self.assertEqual(self.res["label"], "sim")

    def test_raw_metrics_match_definitions(self):
        ref = self.deltas[:, 1, :16].mean(axis=1) > 0
        raw = self.res["raw"]
        self.assertAlmostEqual(raw["uniformity_mean"], ref.mean())
        self.assertAlmostEqual(raw["uniqueness_mean"], swa.pairwise_hd(ref).mean())
        single = self.deltas[:, 1, 16:] > 0
        self.assertAlmostEqual(raw["reliability_vs_temp"]["25"],
                               1 - (single != ref[:, None]).mean())
        hot = self.deltas[:, 2] > 0
        self.assertAlmostEqual(raw["reliability_vs_temp"]["85"],
                               1 - (hot != ref[:, None]).mean())
        self.assertGreater(raw["reliability_vs_temp"]["25"], 0.95)
        self.assertLess(abs(raw["uniqueness_mean"] - 0.5), 0.05)

    def test_keygen_matches_model_enrollment(self):
        for row in self.res["per_tau"]:
            kp = replace(KP, tau=row["tau"])
            n = [rp.enroll_from_mean(self.deltas[b, 1, :16].mean(axis=0), kp).n_passing
                 for b in range(4)]
            self.assertEqual(row["pairs_passing_min"], min(n))
            self.assertEqual(row["pairs_passing_max"], max(n))
        t64 = next(r for r in self.res["per_tau"] if r["tau"] == 64)
        self.assertEqual(t64["enrolled_boards"], 4)
        self.assertEqual(t64["distinct_ids"], 4)
        # 25 °C: 15 races left after enrollment -> 5; -40 / 85 °C: 31 -> 10 each,
        # fewer only if a reconstruction needed re-measurements.
        self.assertLessEqual(t64["reconstructions"], 4 * (5 + 10 + 10))
        self.assertGreater(t64["reconstructions"], 4 * 20)
        self.assertGreater(t64["key_reliability_majority"], 0.99)

    def test_report_written(self):
        with tempfile.TemporaryDirectory() as d:
            swa.write_markdown(self.res, Path(d) / "results.md")
            text = (Path(d) / "results.md").read_text()
        self.assertIn("(sim)", text)
        self.assertNotIn("hardware", text)

    def test_export_run_loads_in_char_analyze(self):
        spec = importlib.util.spec_from_file_location(
            "char_analyze", ROOT / "fpga" / "char" / "analyze.py")
        char = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(char)
        with tempfile.TemporaryDirectory() as d:
            swa.export_run(self.data, Path(d))
            run = char.RunData(Path(d))
            meta = json.loads((Path(d) / "meta.json").read_text())
        self.assertTrue((run.d == self.deltas).all())
        self.assertEqual(meta["delta_magnitude_bias"], -3.0)
        self.assertEqual(meta["source"], "sim")


class LoaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, reps, pairs=4, status=2, board=1, temp=25):
        return [(board, temp, "nan", r, p, 16384, 16384 - (p + 1), status)
                for r in range(reps) for p in range(pairs)]

    def test_incomplete_sweep_dropped(self):
        rows = self.rows(3)[:-1]
        write_csv(self.dir / "a.csv", rows, {"n_pairs": 4})
        data = swa.load([self.dir / "a.csv"])
        self.assertEqual(data.delta.shape, (1, 1, 2, 4))
        self.assertTrue(any("incomplete" in w for w in data.warnings))

    def test_timeout_pair_excluded(self):
        rows = self.rows(2)
        rows[1] = rows[1][:-1] + (6,)
        write_csv(self.dir / "a.csv", rows, {"n_pairs": 4})
        data = swa.load([self.dir / "a.csv"])
        self.assertEqual(data.valid_pairs.tolist(), [True, False, True, True])
        with self.assertRaises(ValueError):
            swa.export_run(data, self.dir / "run")

    def test_duplicate_race_rejected(self):
        write_csv(self.dir / "a.csv", self.rows(2) + self.rows(1), {"n_pairs": 4})
        with self.assertRaisesRegex(ValueError, "twice"):
            swa.load([self.dir / "a.csv"])

    def test_files_append(self):
        write_csv(self.dir / "a.csv", self.rows(2), {"n_pairs": 4})
        write_csv(self.dir / "b.csv", self.rows(3), {"n_pairs": 4})
        data = swa.load([self.dir / "a.csv", self.dir / "b.csv"])
        self.assertEqual(data.delta.shape[2], 5)

    def test_freq_file_rejected(self):
        write_csv(self.dir / "f.csv", [], {"mode": "freq"}, header=HEADER + ",window_ns")
        with self.assertRaisesRegex(ValueError, "mode=freq"):
            swa.load([self.dir / "f.csv"])

    def test_missing_temperature_rejected(self):
        write_csv(self.dir / "a.csv", self.rows(2, board=1) + self.rows(2, board=2, temp=85),
                  {"n_pairs": 4})
        with self.assertRaisesRegex(ValueError, "missing"):
            swa.load([self.dir / "a.csv"])

    def test_enrollment_needs_enough_races(self):
        write_csv(self.dir / "a.csv", self.rows(5), {"n_pairs": 4})
        data = swa.load([self.dir / "a.csv"])
        with self.assertRaisesRegex(ValueError, "at least 16"):
            swa.analyze(data)
        res = swa.analyze(data, n_enroll=2)
        self.assertEqual(res["per_tau"][0]["enroll_failed_boards"], 1)  # 4 < 216 pairs


if __name__ == "__main__":
    unittest.main()
