# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks that analyze.py recovers known parameters from simulated runs."""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import acquire
import analyze
import ro_puf as rp

TRUE = rp.PufParams(sigma_process=0.01, sigma_jitter=3e-4, sigma_tempco=2.5e-5)
TEMPS = (-40.0, 25.0, 85.0)


def sim_run(directory, sigma_tempco2=0.0, chips=6, reps=20, seed=3):
    backend = acquire.SimBackend(TRUE, acquire.SimExtras(sigma_tempco2), chips, seed)
    return acquire.acquire(backend, TEMPS, reps, Path(directory))


class RecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        run = analyze.RunData(sim_run(cls.tmp.name))
        cls.data = run
        cls.result, cls.arrays = analyze.analyze(run)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def assertClose(self, got, want, rel):
        self.assertLess(abs(got - want) / want, rel, f"{got} vs {want}")

    def test_layout(self):
        self.assertEqual(self.data.d.shape, (6, 3, 20, 512))
        self.assertEqual(self.data.t_ref, 25.0)
        self.assertEqual(self.result["label"], "simulated")

    def test_recovers_parameters(self):
        r = self.result
        self.assertClose(r["sigma_process"], TRUE.sigma_process, 0.10)
        self.assertClose(r["sigma_jitter"], TRUE.sigma_jitter, 0.05)
        self.assertClose(r["sigma_tempco"], TRUE.sigma_tempco, 0.15)
        self.assertLess(abs(r["tempco_common_fit"] - TRUE.tempco), 3e-4)

    def test_linear_model_passes_linearity_check(self):
        li = self.result["linearity"]
        self.assertLess(li["fraction_nonlinear"], 0.01)
        self.assertLess(abs(li["chi2_dof_mean"] - 1), 0.2)

    def test_response_metrics_in_range(self):
        r = self.result
        self.assertLess(abs(r["uniformity_mean"] - 0.5), 0.05)
        self.assertLess(abs(r["uniqueness_mean"] - 0.5), 0.05)
        self.assertGreater(r["reliability_vs_temp"]["25"], r["reliability_vs_temp"]["85"])

    def test_outputs_written(self):
        with tempfile.TemporaryDirectory() as out:
            out = Path(out)
            analyze.write_report(self.result, self.arrays, self.data.temps, out)
            analyze.plot(self.result, self.arrays, self.data.temps, out)
            report = (out / "report.md").read_text()
            self.assertIn("(simulated)", report)
            for name in ("delta_hist", "reliability_vs_temp", "tempco_slopes",
                         "linearity"):
                self.assertTrue((out / f"{name}.png").exists(), name)


class NonlinearityTest(unittest.TestCase):
    def test_quadratic_tempco_is_flagged(self):
        with tempfile.TemporaryDirectory() as d:
            result, _ = analyze.analyze(analyze.RunData(sim_run(d, sigma_tempco2=1e-7)))
        self.assertGreater(result["linearity"]["fraction_nonlinear"], 0.2)


class FormatTest(unittest.TestCase):
    def test_incomplete_grid_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            run = sim_run(d, chips=2, reps=3)
            lines = (run / "deltas.csv").read_text().splitlines()
            (run / "deltas.csv").write_text("\n".join(lines[:-1]) + "\n")
            with self.assertRaises(ValueError):
                analyze.RunData(run)

    def test_meta_written(self):
        with tempfile.TemporaryDirectory() as d:
            meta = json.loads((sim_run(d, chips=1, reps=2) / "meta.json").read_text())
        self.assertEqual(meta["source"], "sim")
        self.assertEqual(meta["count_threshold"], 2 ** 14)
        self.assertEqual(meta["true_params"]["sigma_jitter"], TRUE.sigma_jitter)

    def test_x_transform_inverts_race(self):
        n = 2 ** 14
        fa, fb = 250e6, 249e6
        d = rp.race(fa, fb, rp.PufParams(sigma_jitter=0.0), np.random.default_rng(0),
                    shape=(2000,))
        self.assertAlmostEqual(float(analyze.to_x(d, n).mean()), np.log(fa / fb), 5)
        d = rp.race(fb, fa, rp.PufParams(sigma_jitter=0.0), np.random.default_rng(0),
                    shape=(2000,))
        self.assertAlmostEqual(float(analyze.to_x(d, n).mean()), np.log(fb / fa), 5)

    def test_hardware_backend_is_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            acquire.HardwareBackend()


if __name__ == "__main__":
    unittest.main()
