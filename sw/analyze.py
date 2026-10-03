# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""PUF metrics from raw RO-PUF measurements, same definitions as the model (1A).

    python3 sw/analyze.py runs/board*_*.csv --out runs/report
    python3 sw/analyze.py runs/*.csv --export-run fpga/char/runs/<date>-<board>

Input: CSV files written by fpga/char/syscon/measure_pairs.tcl (mode=pairs):
'#key=value' metadata lines, then

    board,temp_c,vdd_v,rep,pair,count_a,count_b,status

Several files may be given (boards, temperatures, or more repetitions of the
same board and temperature, which are appended in file order).

delta = count_a - count_b; response bit = delta > 0 (a tie reads as 0, as
in rtl/puf_meas.v). Then, exactly as model/puf_montecarlo.py and
model/ro_puf.py define them:

Raw 512-pair response
    reference response = sign of the mean of the first n_enroll (16) races
    at the first enrollment temperature (default 25 °C); uniformity =
    fraction of ones; uniqueness = fractional Hamming distance between
    boards; reliability(T) = 1 - mean fractional Hamming distance of single
    races at T to the reference (races used for enrollment excluded).

Key generation, per mask threshold τ
    enrollment from the same 16-race means (ro_puf.enroll_from_mean: mask,
    first 216 pairs, SECDED (72,64) syndromes, 32-bit KCV); then the
    recorded races not used for enrollment are replayed, in order, as
    reconstructions: 3 races -> majority vote -> decode; a detected block or
    a KCV mismatch consumes the next 3 races (up to 3 re-measurements). A
    reconstruction that runs out of races is not counted.

Differences from the model run, by necessity: reconstructions happen at the
measured temperatures, not T ~ U(-40, 85) °C, and their number is limited by
the races recorded (the model used 1000 per chip). Zero-event rates carry a
95 % upper bound of 3/n.

Outputs: <out>/results.json and <out>/results.md (and figures), labelled
with the data source from the CSV metadata.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
import ro_puf as rp  # noqa: E402
import secded  # noqa: E402

COLUMNS = ["board", "temp_c", "vdd_v", "rep", "pair", "count_a", "count_b", "status"]
STATUS_DONE = 2
STATUS_TIMEOUT = 4
DEFAULT_TAUS = (0, 16, 32, 64, 128)
# |DELTA| of rtl/ropuf is on average 3 counts below the ideal (rtl/ropuf/README.md).
RTL_DELTA_MAGNITUDE_BIAS = -3.0


# --- loading ----------------------------------------------------------------

@dataclass
class RawData:
    """Races as dense arrays [board, temp, rep, pair] at one supply voltage."""
    boards: list            # board labels (str), sorted
    temps: np.ndarray       # (T,) degC, sorted
    delta: np.ndarray       # (B, T, R, P) int64
    valid_pairs: np.ndarray  # (P,) bool: no timeout on any board / temperature
    log2n: int
    vdd_v: float
    source: str
    files: list
    warnings: list

    @property
    def bits(self):
        return self.delta > 0

    @property
    def n_pairs(self):
        return self.delta.shape[3]


def read_csv(path):
    """-> (metadata dict, rows (N, 8) float array)."""
    meta, lines = {}, []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                if "=" in line:
                    k, v = line[1:].split("=", 1)
                    meta[k.strip()] = v.strip()
                continue
            lines.append(line)
    if not lines or lines[0].split(",")[:len(COLUMNS)] != COLUMNS:
        raise ValueError(f"{path}: header must start with {','.join(COLUMNS)}")
    if meta.get("mode", "pairs") != "pairs":
        raise ValueError(f"{path}: mode={meta['mode']} files are not race data")
    rows = [ln.split(",")[:len(COLUMNS)] for ln in lines[1:]]
    if any(len(r) != len(COLUMNS) for r in rows):
        raise ValueError(f"{path}: short row")
    data = np.array([[float(x) for x in r] for r in rows]) if rows else np.zeros((0, 8))
    return meta, data


def load(paths, n_pairs=None):
    warnings = []
    sources, log2ns = set(), set()
    # (board, temp) -> list of (rep, pair, delta, status, vdd) with reps
    # offset so that later files append to earlier ones.
    races = defaultdict(list)
    offset = Counter()
    for path in paths:
        meta, a = read_csv(path)
        sources.add(meta.get("source", "unknown"))
        if "log2n" in meta:
            log2ns.add(int(meta["log2n"]))
        if n_pairs is None and "n_pairs" in meta:
            n_pairs = int(meta["n_pairs"])
        file_reps = defaultdict(set)
        for board, temp, vdd, rep, pair, ca, cb, st in a:
            key = (_board_label(board), float(temp))
            file_reps[key].add(int(rep))
            races[key].append((int(rep) + offset[key], int(pair), int(ca) - int(cb),
                               int(st), vdd))
        for key, reps in file_reps.items():
            offset[key] += max(reps) + 1
    if not races:
        raise ValueError("no races in the input files")
    if len(log2ns) > 1:
        raise ValueError(f"files disagree on log2n: {sorted(log2ns)}")
    log2n = log2ns.pop() if log2ns else 14
    if n_pairs is None:
        n_pairs = 1 + max(r[1] for v in races.values() for r in v)

    vdds = Counter(r[4] if not np.isnan(r[4]) else -1.0
                   for v in races.values() for r in v)
    vdd = vdds.most_common(1)[0][0]
    if len(vdds) > 1:
        warnings.append(f"several supply values {sorted(vdds)}; analysing only "
                        f"the most common ({'not recorded' if vdd < 0 else vdd})")

    boards = sorted({b for b, _ in races}, key=_board_sort_key)
    temps = np.array(sorted({t for _, t in races}))
    timeout_pairs = set()
    per_cond = {}
    for key, rs in races.items():
        rs = [r for r in rs if (r[4] if not np.isnan(r[4]) else -1.0) == vdd]
        if not rs:
            continue
        reps = defaultdict(dict)
        for rep, pair, d, st, _ in rs:
            if pair >= n_pairs:
                raise ValueError(f"pair {pair} >= n_pairs {n_pairs}")
            if pair in reps[rep]:
                raise ValueError(f"board {key[0]} {key[1]:g} °C: rep {rep} pair "
                                 f"{pair} appears twice")
            if not st & STATUS_DONE:
                raise ValueError(f"board {key[0]} {key[1]:g} °C rep {rep} pair "
                                 f"{pair}: status {st} without DONE")
            if st & STATUS_TIMEOUT:
                timeout_pairs.add(pair)
            reps[rep][pair] = d
        complete = [r for r in sorted(reps) if len(reps[r]) == n_pairs]
        if len(complete) < len(reps):
            warnings.append(f"board {key[0]} {key[1]:g} °C: dropped "
                            f"{len(reps) - len(complete)} incomplete sweep(s)")
        per_cond[key] = np.array([[reps[r][p] for p in range(n_pairs)]
                                  for r in complete], dtype=np.int64).reshape(-1, n_pairs)

    missing = [(b, t) for b in boards for t in temps if (b, float(t)) not in per_cond
               or per_cond[(b, float(t))].shape[0] == 0]
    if missing:
        raise ValueError("every board needs data at every temperature; missing: "
                         + ", ".join(f"board {b} at {t:g} °C" for b, t in missing))
    n_rep = min(v.shape[0] for v in per_cond.values())
    if any(v.shape[0] > n_rep for v in per_cond.values()):
        warnings.append(f"using the first {n_rep} sweeps of every board and "
                        "temperature (the smallest count recorded)")
    delta = np.array([[per_cond[(b, float(t))][:n_rep] for t in temps] for b in boards])
    valid = np.ones(n_pairs, dtype=bool)
    if timeout_pairs:
        valid[sorted(timeout_pairs)] = False
        warnings.append(f"{len(timeout_pairs)} pair(s) timed out at least once and are "
                        f"excluded: {sorted(timeout_pairs)[:20]}")
    source = sources.pop() if len(sources) == 1 else "mixed(" + ",".join(sorted(sources)) + ")"
    return RawData(boards, temps, delta, valid, log2n, vdd, source,
                   [str(p) for p in paths], warnings)


def _board_label(x):
    return str(int(x)) if float(x).is_integer() else str(x)


def _board_sort_key(b):
    try:
        return (0, float(b), b)
    except ValueError:
        return (1, 0.0, b)


# --- key reconstruction from recorded races -----------------------------------

@dataclass
class Recon:
    key_bits: np.ndarray   # (n_key_bits,) bool
    failed: bool
    attempts: int
    kcv_caught: bool
    first_votes: np.ndarray  # (n_votes, n_key_bits) bool


class RaceStream:
    """Recorded single-race bits (M, P) consumed n_votes rows at a time."""

    def __init__(self, bits, n_votes):
        self.bits, self.n_votes, self.pos = bits, n_votes, 0

    def next_round(self, pairs):
        if self.pos + self.n_votes > self.bits.shape[0]:
            return None
        v = self.bits[self.pos:self.pos + self.n_votes][:, pairs]
        self.pos += self.n_votes
        return v


def _kcv_ok(words, helper, kp):
    return bool(rp._kcv_matches(words.reshape(1, -1), helper.kcv, kp.kcv_bits)[0])


def reconstruct_recorded(stream: RaceStream, helper, kp):
    """One reconstruction with the rules of ro_puf.reconstruct(), using
    recorded races instead of simulated ones. None if the races run out."""
    nb, n = kp.n_blocks, secded.N
    votes = stream.next_round(helper.pairs)
    if votes is None:
        return None
    maj = votes.sum(axis=0) > kp.n_votes // 2
    words, det = secded.decode(maj.reshape(nb, n), helper.syndromes)
    kcv_bad = not det.any() and not _kcv_ok(words, helper, kp)
    caught, attempts = kcv_bad, 1
    for _ in range(kp.max_remeasure):
        redo = det | kcv_bad  # KCV mismatch: redo every block
        if not redo.any():
            break
        v = stream.next_round(helper.pairs)
        if v is None:
            return None
        m = (v.sum(axis=0) > kp.n_votes // 2).reshape(nb, n)
        w, d = secded.decode(m[redo], helper.syndromes[redo])
        words[redo], det[redo] = w, d
        attempts += 1
        kcv_bad = not det.any() and not _kcv_ok(words, helper, kp)
        caught = caught or kcv_bad
    return Recon(words.reshape(-1), bool(det.any() or kcv_bad), attempts, caught, votes)


# --- metrics ------------------------------------------------------------------

def pairwise_hd(bits):
    b = bits.astype(np.int64)
    diff = b @ (1 - b).T + (1 - b) @ b.T
    iu = np.triu_indices(b.shape[0], k=1)
    return diff[iu] / b.shape[1]


def _temp_index(temps, t):
    k = int(np.abs(temps - t).argmin())
    if abs(temps[k] - t) > 1e-9:
        raise ValueError(f"enrollment temperature {t:g} °C not in the data "
                         f"({', '.join(f'{x:g}' for x in temps)})")
    return k


def enrollment_means(data: RawData, kp):
    """(B, n_enroll_temps, P) mean delta of the first n_enroll races."""
    if data.delta.shape[2] < kp.n_enroll:
        raise ValueError(f"need at least {kp.n_enroll} races per pair for enrollment, "
                         f"have {data.delta.shape[2]}")
    ks = [_temp_index(data.temps, t) for t in kp.enroll_temps_c]
    return data.delta[:, ks, :kp.n_enroll].mean(axis=2), ks


def raw_metrics(data: RawData, kp):
    mean, ks = enrollment_means(data, kp)
    v = data.valid_pairs
    ref = mean[:, 0, v] > 0                                   # (B, P')
    rel, worst, n_single = {}, {}, {}
    for k, t in enumerate(data.temps):
        start = kp.n_enroll if k in ks else 0
        single = data.bits[:, k, start:][:, :, v]             # (B, R', P')
        if single.shape[1] == 0:
            continue
        hd = (single != ref[:, None, :]).mean(axis=2)         # (B, R')
        rel[f"{t:g}"] = float(1 - hd.mean())
        worst[f"{t:g}"] = float(1 - hd.mean(axis=1).max())
        n_single[f"{t:g}"] = int(single.shape[0] * single.shape[1])
    out = {
        "uniformity_mean": float(ref.mean()),
        "uniformity_std_boards": float(ref.mean(axis=1).std()),
        "reliability_vs_temp": rel,
        "reliability_worst_board_vs_temp": worst,
        "single_races_per_temp": n_single,
        "sigma_delta_counts": float(mean[:, 0, v].std()),
        "tie_fraction": float((data.delta[..., v] == 0).mean()),
        "pairs_used": int(v.sum()),
    }
    if len(data.boards) > 1:
        inter = pairwise_hd(ref)
        out["uniqueness_mean"] = float(inter.mean())
        out["uniqueness_std"] = float(inter.std())
    return out


def keygen_metrics(data: RawData, kp, mean, ks):
    enr = []
    for b in range(len(data.boards)):
        md = mean[b].copy()
        md[:, ~data.valid_pairs] = np.nan  # timed-out pairs never pass the mask
        enr.append(rp.enroll_from_mean(md, kp))
    passing = np.array([e.n_passing for e in enr])
    ok = [i for i, e in enumerate(enr) if e.ok]
    row = {
        "tau": kp.tau,
        "pairs_passing_mean": float(passing.mean()),
        "pairs_passing_min": int(passing.min()),
        "pairs_passing_max": int(passing.max()),
        "pairs_passing_per_board": dict(zip(data.boards, passing.tolist())),
        "enroll_failed_boards": len(data.boards) - len(ok),
        "enrolled_boards": len(ok),
    }
    if not ok:
        return row
    keys = np.array([enr[i].key_bits for i in ok])
    row["key_uniformity"] = float(keys.mean())
    if len(ok) > 1:
        row["key_uniqueness"] = float(pairwise_hd(keys).mean())
    row["distinct_ids"] = len({rp.device_id(rp.derive_key(k)) for k in keys})

    n_tot = n_fail = n_silent = n_retry = n_caught = n_recovered = 0
    attempts = single_err = maj_err = n_bits = 0
    worst = boards_with_fail = 0
    by_temp = {f"{t:g}": [0, 0] for t in data.temps}    # [reconstructions, failures]
    for i in ok:
        e = enr[i]
        board_fail = 0
        for k, t in enumerate(data.temps):
            start = kp.n_enroll if k in ks else 0
            stream = RaceStream(data.bits[i, k, start:], kp.n_votes)
            while True:
                r = reconstruct_recorded(stream, e.helper, kp)
                if r is None:
                    break
                wrong = bool((r.key_bits != e.key_bits).any())
                n_tot += 1
                n_fail += r.failed
                n_silent += wrong and not r.failed
                n_retry += r.attempts > 1
                n_caught += r.kcv_caught
                n_recovered += r.kcv_caught and not r.failed and not wrong
                attempts += r.attempts
                board_fail += r.failed or wrong
                by_temp[f"{t:g}"][0] += 1
                by_temp[f"{t:g}"][1] += r.failed or wrong
                single_err += int((r.first_votes != e.key_bits).sum())
                maj = r.first_votes.sum(axis=0) > kp.n_votes // 2
                maj_err += int((maj != e.key_bits).sum())
                n_bits += e.key_bits.size
        worst = max(worst, board_fail)
        boards_with_fail += board_fail > 0
    row["reconstructions"] = n_tot
    if n_tot:
        row.update({
            "detected_failures": n_fail,
            "silent_wrong_keys": n_silent,
            "kcv_caught": n_caught,
            "kcv_caught_recovered": n_recovered,
            "needed_remeasure": n_retry,
            "mean_attempts": attempts / n_tot,
            "worst_board_failures": worst,
            "boards_with_failures": boards_with_fail,
            "key_reliability_single": 1 - single_err / (n_bits * kp.n_votes),
            "key_reliability_majority": 1 - maj_err / n_bits,
            "zero_event_bound": 3.0 / n_tot,
            "by_temp": {t: {"reconstructions": v[0], "failures": v[1]}
                        for t, v in by_temp.items()},
        })
    return row


def keygen_params(enroll_temps=(25.0,), n_enroll=16):
    return rp.KeyGenParams(enroll_temps_c=tuple(float(t) for t in enroll_temps),
                           n_enroll=n_enroll)


def analyze(data: RawData, taus=DEFAULT_TAUS, enroll_temps=(25.0,), n_enroll=16):
    kp0 = keygen_params(enroll_temps, n_enroll)
    mean, ks = enrollment_means(data, kp0)
    return {
        "label": data.source,
        "files": data.files,
        "boards": data.boards,
        "temps_c": data.temps.tolist(),
        "races_per_pair_per_temp": int(data.delta.shape[2]),
        "n_pairs": data.n_pairs,
        "count_threshold": 1 << data.log2n,
        "vdd_v": None if data.vdd_v < 0 else data.vdd_v,
        "keygen": {"n_enroll": kp0.n_enroll, "n_key_bits": kp0.n_key_bits,
                   "n_votes": kp0.n_votes, "max_remeasure": kp0.max_remeasure,
                   "kcv_bits": kp0.kcv_bits, "enroll_temps_c": list(kp0.enroll_temps_c)},
        "warnings": data.warnings,
        "raw": raw_metrics(data, kp0),
        "per_tau": [keygen_metrics(data, replace(kp0, tau=float(t)), mean, ks)
                    for t in taus],
    }


# --- report -------------------------------------------------------------------

def _rate(k, n):
    if n == 0:
        return "–"
    return f"0 (< {3 / n:.1e})" if k == 0 else f"{k / n:.2e} ({k})"


def _ber(rel, n_bits):
    ber = 1 - rel
    return f"0 (< {3 / n_bits:.1e})" if ber <= 0 else f"{ber:.2e}"


def write_markdown(res, path):
    lab = res["label"]
    raw = res["raw"]
    nb = len(res["boards"])
    lines = [
        f"# RO-PUF metrics from recorded races ({lab})",
        "",
        f"Data source: **{lab}** (from the CSV metadata). Generated by "
        "`sw/analyze.py` with the definitions of `model/puf_montecarlo.py`.",
        "",
        f"{nb} board(s): {', '.join(res['boards'])}; temperatures "
        f"{', '.join(f'{t:g}' for t in res['temps_c'])} °C; "
        f"{res['races_per_pair_per_temp']} races per pair per temperature; "
        f"{raw['pairs_used']} of {res['n_pairs']} pairs used; counter threshold "
        f"{res['count_threshold']}; enrollment at "
        f"{' + '.join(f'{t:g}' for t in res['keygen']['enroll_temps_c'])} °C.",
        "",
    ]
    if res["warnings"]:
        lines += ["**Warnings:**", ""] + [f"- {w}" for w in res["warnings"]] + [""]
    lines += [
        f"## Raw {raw['pairs_used']}-pair response ({lab})",
        "",
        f"- uniformity: {raw['uniformity_mean']:.2%} ± {raw['uniformity_std_boards']:.2%} "
        "over boards",
    ]
    if "uniqueness_mean" in raw:
        lines.append(f"- uniqueness: {raw['uniqueness_mean']:.2%} ± "
                     f"{raw['uniqueness_std']:.2%}")
    else:
        lines.append("- uniqueness: needs at least 2 boards")
    lines += [
        f"- σ of the mean Δ: {raw['sigma_delta_counts']:.1f} counts",
        f"- races with Δ = 0 (dead zone): {raw['tie_fraction']:.3%}",
        "",
        "| temperature (°C) | reliability, mean | worst board | single races |",
        "|---|---|---|---|",
    ]
    for t, r in raw["reliability_vs_temp"].items():
        lines.append(f"| {t} | {r:.3%} | {raw['reliability_worst_board_vs_temp'][t]:.3%} "
                     f"| {raw['single_races_per_temp'][t]} |")
    lines += [
        "",
        f"## Key generation per τ ({lab})",
        "",
        "Reconstructions replay the recorded races not used for enrollment, at "
        "the measured temperatures. Rates with zero events show the 95 % upper "
        "bound 3/n.",
        "",
        "| τ (counts) | pairs passing mean [min–max] | boards failing enrollment | "
        "reconstructions | key BER 1 race | key BER maj-3 | detected failure rate | "
        "silent wrong-key rate | needed re-measure | boards with ≥1 failure | "
        "key uniqueness | distinct IDs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for t in res["per_tau"]:
        head = (f"| {t['tau']:g} | {t['pairs_passing_mean']:.0f} [{t['pairs_passing_min']}–"
                f"{t['pairs_passing_max']}] | {t['enroll_failed_boards']}/{nb} |")
        n = t.get("reconstructions", 0)
        if not t["enrolled_boards"] or not n:
            lines.append(head + f" {n} | – | – | – | – | – | – | – | – |")
            continue
        lines.append(
            head + f" {n} | {_ber(t['key_reliability_single'], n * 216 * 3)} | "
            f"{_ber(t['key_reliability_majority'], n * 216)} | "
            f"{_rate(t['detected_failures'], n)} | {_rate(t['silent_wrong_keys'], n)} | "
            f"{t['needed_remeasure'] / n:.2%} | "
            f"{t['boards_with_failures']}/{t['enrolled_boards']} | "
            + (f"{t['key_uniqueness']:.2%}" if "key_uniqueness" in t else "–")
            + f" | {t['distinct_ids']}/{t['enrolled_boards']} |")
    lines += ["", "![Reliability vs temperature](reliability_vs_temp.png)",
              "![Pairs passing the mask](pairs_passing.png)"]
    if "uniqueness_mean" in raw:
        lines.append("![Hamming distances](hd_hist.png)")
    Path(path).write_text("\n".join(lines) + "\n")


def plot(res, data: RawData, kp, out):
    import puf_plots as pp
    plt = pp.plt
    lab = res["label"]
    note = f"{lab.upper()} data (sw/analyze.py)"
    raw = res["raw"]

    fig, ax = plt.subplots(figsize=(7, 4.2))
    temps = [float(t) for t in raw["reliability_vs_temp"]]
    ax.plot(temps, [100 * v for v in raw["reliability_vs_temp"].values()],
            color=pp.SERIES[0], marker="o", label="mean over boards")
    ax.plot(temps, [100 * v for v in raw["reliability_worst_board_vs_temp"].values()],
            color=pp.SERIES[1], marker="o", label="worst board")
    ax.set_xlabel("temperature (°C)")
    ax.set_ylabel("reliability, 1 race (%)")
    ax.legend(loc="lower center")
    pp._finish(fig, "Raw reliability vs temperature", out / "reliability_vs_temp.png",
               label=lab, note=note)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    t = [x["tau"] for x in res["per_tau"]]
    mean = np.array([x["pairs_passing_mean"] for x in res["per_tau"]])
    lo = mean - [x["pairs_passing_min"] for x in res["per_tau"]]
    hi = [x["pairs_passing_max"] for x in res["per_tau"]] - mean
    ax.errorbar(t, mean, yerr=[lo, hi], color=pp.SERIES[0], marker="o", capsize=3)
    ax.axhline(kp.n_key_bits, color=pp.INK_2, linestyle="--", linewidth=1)
    ax.set_xlabel("mask threshold τ (counts)")
    ax.set_ylabel(f"pairs with |Δ̄| ≥ τ (of {res['n_pairs']})")
    pp._finish(fig, "Pairs passing the mask: mean, min–max over boards",
               out / "pairs_passing.png", label=lab, note=note)

    if "uniqueness_mean" in raw:
        mean_d, ks = enrollment_means(data, kp)
        v = data.valid_pairs
        ref = mean_d[:, 0, v] > 0
        k = ks[0]
        intra = (data.bits[:, k, kp.n_enroll:][:, :, v] != ref[:, None, :]).mean(axis=2)
        fig, ax = plt.subplots(figsize=(7, 4.2))
        bins = np.linspace(0, 0.7, 71)
        ax.hist(intra.ravel(), bins=bins, density=True, color=pp.SERIES[0],
                alpha=0.85, label=f"intra-board, {data.temps[k]:g} °C")
        ax.hist(pairwise_hd(ref), bins=bins, density=True, color=pp.SERIES[1],
                alpha=0.85, label="inter-board")
        ax.set_xlabel("fractional Hamming distance")
        ax.set_ylabel("density")
        ax.legend(loc="upper center")
        pp._finish(fig, "Hamming distance distributions", out / "hd_hist.png",
                   label=lab, note=note)


def export_run(data: RawData, out: Path):
    """Write deltas.csv + meta.json for fpga/char/analyze.py."""
    if not data.valid_pairs.all():
        raise ValueError("cannot export: some pairs timed out (fpga/char/analyze.py "
                         "needs every pair)")
    out.mkdir(parents=True, exist_ok=True)
    b, t, r, p = np.meshgrid(*(np.arange(n) for n in data.delta.shape), indexing="ij")
    vdd = np.nan if data.vdd_v < 0 else data.vdd_v
    rows = np.column_stack([b.ravel(), data.temps[t.ravel()], np.full(b.size, vdd),
                            r.ravel(), p.ravel(), data.delta.ravel()])
    np.savetxt(out / "deltas.csv", rows, delimiter=",", comments="",
               header="chip,temp_c,vdd_v,rep,pair,delta",
               fmt=["%d", "%g", "%g", "%d", "%d", "%d"])
    meta = {"source": data.source, "count_threshold": 1 << data.log2n,
            "n_ro": 2 * data.n_pairs, "delta_magnitude_bias": RTL_DELTA_MAGNITUDE_BIAS,
            "boards": {str(i): b for i, b in enumerate(data.boards)},
            "raw_files": data.files,
            "notes": "Exported by sw/analyze.py. Add board serial, bitstream "
                     "SHA-256, chamber and operator notes."}
    (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", type=Path, nargs="+", help="raw CSV(s) from measure_pairs.tcl")
    ap.add_argument("--out", type=Path, help="report directory")
    ap.add_argument("--taus", type=float, nargs="+", default=list(DEFAULT_TAUS))
    ap.add_argument("--enroll-temps", type=float, nargs="+", default=[25.0],
                    help="enrollment temperature(s), must be in the data (°C)")
    ap.add_argument("--n-enroll", type=int, default=16,
                    help="races averaged for enrollment (1A: 16; lower only for quick looks)")
    ap.add_argument("--export-run", type=Path,
                    help="also write an fpga/char run directory (deltas.csv, meta.json)")
    ap.add_argument("--no-plots", action="store_true")
    args = ap.parse_args(argv)
    if not args.out and not args.export_run:
        ap.error("give --out and/or --export-run")

    data = load(args.csv)
    for w in data.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.export_run:
        export_run(data, args.export_run)
        print(f"wrote {args.export_run}/deltas.csv and meta.json")
    if args.out:
        res = analyze(data, args.taus, args.enroll_temps, args.n_enroll)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "results.json").write_text(json.dumps(res, indent=1) + "\n")
        write_markdown(res, args.out / "results.md")
        if not args.no_plots:
            kp = keygen_params(args.enroll_temps, args.n_enroll)
            plot(res, data, kp, args.out)
        raw = res["raw"]
        print(f"{res['label']} data, {len(data.boards)} board(s): uniformity "
              f"{raw['uniformity_mean']:.2%}, reliability "
              + ", ".join(f"{t} °C {v:.2%}" for t, v in raw["reliability_vs_temp"].items())
              + f" -> {args.out}")


if __name__ == "__main__":
    main()
