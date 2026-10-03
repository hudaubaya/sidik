# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Monte Carlo sweep of the RO-PUF key generator model (ro_puf.py).

    python3 model/puf_montecarlo.py --out docs/puf-model

Writes results.json, results.md and the figures into --out. Every number is
a model output, not a measurement.
"""

import argparse
import json
import platform
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

import ro_puf as rp

SIGMAS = (0.005, 0.01, 0.02)
TAUS = (0, 16, 32, 64, 128)
TEMP_RANGE_C = (-40.0, 85.0)
TEMP_GRID_C = (-40, -20, 0, 25, 50, 70, 85)
# Enrollment temperature sets. The first is the baseline used everywhere else.
ENROLL_MODES = (("25 °C", (25.0,)),
                ("25 + 85 °C", (25.0, 85.0)),
                ("−40 + 85 °C", (-40.0, 85.0)))


def pairwise_hd(bits):
    """Fractional Hamming distance of every unordered row pair of (m, n) bits."""
    b = bits.astype(np.int64)
    n = b.shape[1]
    diff = b @ (1 - b).T + (1 - b) @ b.T
    iu = np.triu_indices(b.shape[0], k=1)
    return diff[iu] / n


def zero_bound(n):
    """95 % upper bound on a rate with 0 events in n trials (rule of three)."""
    return 3.0 / n


def run_reconstructions(chips, mean_delta, ok, seeds, kp, n_recon):
    n_tot = n_fail = n_silent = n_retry = n_caught = n_recovered = 0
    attempts_sum = single_err = maj_err = n_bits = 0
    worst_chip_fail = chips_with_fail = 0
    for i in ok:
        e = rp.enroll_from_mean(mean_delta[i], kp)
        rng = np.random.default_rng(seeds[i])
        temps = rng.uniform(*TEMP_RANGE_C, n_recon)
        r = rp.reconstruct(chips[i], e.helper, temps, kp, rng)
        wrong = (r.key_bits != e.key_bits).any(axis=1)
        n_tot += n_recon
        n_fail += int(r.failed.sum())
        n_silent += int((wrong & ~r.failed).sum())
        n_retry += int((r.attempts > 1).sum())
        n_caught += int(r.kcv_caught.sum())
        n_recovered += int((r.kcv_caught & ~r.failed & ~wrong).sum())
        attempts_sum += int(r.attempts.sum())
        chip_fail = int((r.failed | wrong).sum())
        worst_chip_fail = max(worst_chip_fail, chip_fail)
        chips_with_fail += chip_fail > 0
        v = r.first_votes
        single_err += int((v != e.key_bits).sum())
        maj_err += int(((v.sum(axis=1) > kp.n_votes // 2) != e.key_bits).sum())
        n_bits += v.shape[0] * v.shape[2]
    return {
        "kcv_bits": kp.kcv_bits,
        "reconstructions": n_tot,
        "detected_failures": n_fail,
        "silent_wrong_keys": n_silent,
        "kcv_caught": n_caught,
        "kcv_caught_recovered": n_recovered,
        "needed_remeasure": n_retry,
        "mean_attempts": attempts_sum / n_tot,
        "worst_chip_failures": worst_chip_fail,
        "chips_with_failures": int(chips_with_fail),
        "key_reliability_single": 1 - single_err / (n_bits * kp.n_votes),
        "key_reliability_majority": 1 - maj_err / n_bits,
        "zero_event_bound": zero_bound(n_tot),
    }


def enroll_stats(mean_delta, kp, n_chips):
    """Enroll every chip; return (enrollments, enrolled chip indices, row)."""
    enr = [rp.enroll_from_mean(md, kp) for md in mean_delta]
    passing = np.array([e.n_passing for e in enr])
    ok = [i for i, e in enumerate(enr) if e.ok]
    row = {
        "pairs_passing_mean": float(passing.mean()),
        "pairs_passing_min": int(passing.min()),
        "pairs_passing_max": int(passing.max()),
        "enroll_failed_chips": n_chips - len(ok),
        "enrolled_chips": len(ok),
    }
    if ok:
        keys = np.array([enr[i].key_bits for i in ok])
        ids = {rp.device_id(rp.derive_key(k)) for k in keys}
        hd = pairwise_hd(keys) if len(ok) > 1 else np.array([np.nan])
        row.update({
            "key_uniformity": float(keys.mean()),
            "key_uniqueness": float(np.nanmean(hd)),
            "distinct_ids": len(ids),
        })
    return enr, ok, row


def run_sigma(sigma, n_chips, n_recon, n_raw, seed):
    p = rp.PufParams(sigma_process=sigma)
    root = np.random.SeedSequence([seed, round(sigma * 1e6)])
    # Children 0..2+len(TAUS) keep their meaning across versions; extra
    # enrollment modes take the children after them.
    n_streams = 3 + len(TAUS) + len(ENROLL_MODES) - 1
    streams = [s.spawn(n_streams) for s in root.spawn(n_chips)]
    chips = [rp.Chip(p, np.random.default_rng(s[0])) for s in streams]
    mode_delta = []
    for m, (_, temps) in enumerate(ENROLL_MODES):
        kp_m = rp.KeyGenParams(enroll_temps_c=temps)
        idx = 1 if m == 0 else 3 + len(TAUS) + m - 1
        mode_delta.append(np.array([
            rp.measure_enrollment(c, kp_m, np.random.default_rng(s[idx]))
            for c, s in zip(chips, streams)]))
    mean_delta = mode_delta[0]  # (chips, 1, n_pairs), enrolled at 25 C
    response = mean_delta[:, 0] > 0

    # Raw 512-bit metrics (independent of tau).
    ber_vs_t = {}
    intra = {}
    raw_rngs = [np.random.default_rng(s[2]) for s in streams]
    for t in TEMP_GRID_C:
        errs = []
        for c, rng, ref in zip(chips, raw_rngs, response):
            fa, fb = c.pair_freqs(t)
            d = rp.race(fa, fb, p, rng, shape=(n_raw, p.n_pairs))
            errs.append(((d > 0) != ref).mean(axis=1))
        errs = np.array(errs)  # (chips, n_raw) fractional intra-chip HD
        ber_vs_t[t] = float(errs.mean())
        intra[t] = errs.ravel()
    inter = pairwise_hd(response)
    raw = {
        "uniformity_mean": float(response.mean()),
        "uniformity_std_chips": float(response.mean(axis=1).std()),
        "uniqueness_mean": float(inter.mean()),
        "uniqueness_std": float(inter.std()),
        "reliability_vs_temp": {str(t): 1 - v for t, v in ber_vs_t.items()},
        "sigma_delta_counts": float(mean_delta.std()),
    }

    per_tau = []
    for ti, tau in enumerate(TAUS):
        kp = rp.KeyGenParams(tau=float(tau))
        # Reconstructions reuse the same streams for every variant (paired).
        seeds = {i: streams[i][3 + ti] for i in range(n_chips)}
        _, ok, base = enroll_stats(mean_delta, kp, n_chips)
        row = {"tau": tau, **base}
        if ok:
            row.update(run_reconstructions(chips, mean_delta, ok, seeds, kp, n_recon))
            no_kcv = replace(kp, kcv_bits=0)
            row["without_kcv"] = run_reconstructions(chips, mean_delta, ok, seeds,
                                                     no_kcv, n_recon)
        row["enroll_modes"] = {}
        for (label, temps), md in zip(ENROLL_MODES, mode_delta):
            kp_m = replace(kp, enroll_temps_c=temps)
            if len(temps) == 1:
                mode = {k: v for k, v in row.items()
                        if k not in ("tau", "without_kcv", "enroll_modes")}
            else:
                _, ok_m, mode = enroll_stats(md, kp_m, n_chips)
                if ok_m:
                    mode.update(run_reconstructions(chips, md, ok_m, seeds, kp_m,
                                                    n_recon))
            mode["enroll_temps_c"] = list(temps)
            row["enroll_modes"][label] = mode
        per_tau.append(row)

    hist = {"inter": inter.tolist(),
            "intra_25": intra[25].tolist(),
            "intra_worst": np.maximum(intra[-40], intra[85]).tolist()}
    return {"sigma_process": sigma, "params": asdict(p), "raw": raw,
            "per_tau": per_tau, "hist": hist}


def fmt_rate(k, n):
    if k == 0:
        return f"0 (< {3 / n:.1e})"
    return f"{k / n:.2e} ({k})"


def fmt_ber(reliability, n_bits):
    ber = 1 - reliability
    return f"0 (< {3 / n_bits:.1e})" if ber <= 0 else f"{ber:.2e}"


def write_tables(results, out):
    lines = [
        "# RO-PUF key generator: Monte Carlo results (model)",
        "",
        "Generated by `model/puf_montecarlo.py`. **Every number below is a model",
        "output, not a hardware measurement.** Rates with zero events show the",
        "95 % upper bound (rule of three) in parentheses.",
        "",
        "## Raw 512-pair response (model)",
        "",
        "| σ_process | σ_Δ (counts) | uniformity | uniqueness | reliability 25 °C | "
        "reliability −40 °C | reliability 85 °C |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        raw = r["raw"]
        rel = raw["reliability_vs_temp"]
        lines.append(
            f"| {r['sigma_process']:.1%} | {raw['sigma_delta_counts']:.0f} | "
            f"{raw['uniformity_mean']:.2%} ± {raw['uniformity_std_chips']:.2%} | "
            f"{raw['uniqueness_mean']:.2%} ± {raw['uniqueness_std']:.2%} | "
            f"{rel['25']:.2%} | {rel['-40']:.2%} | {rel['85']:.2%} |")
    lines += [
        "",
        "## Key generation per τ (model)",
        "",
        "Reconstructions at T ~ U(−40, 85) °C, enrollment at 25 °C, with the",
        "32-bit key-check value (KCV) in the helper data.",
        "",
        "| σ_process | τ (counts) | pairs passing mean [min–max] | chips failing enrollment | "
        "key BER 1 meas | key BER maj-3 | detected failure rate | "
        "silent wrong-key rate | needed re-measure | chips with ≥1 failure | "
        "worst chip failures / recon | key uniqueness | distinct IDs |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for t in r["per_tau"]:
            head = (f"| {r['sigma_process']:.1%} | {t['tau']} | "
                    f"{t['pairs_passing_mean']:.0f} [{t['pairs_passing_min']}–"
                    f"{t['pairs_passing_max']}] | {t['enroll_failed_chips']}/"
                    f"{t['enroll_failed_chips'] + t['enrolled_chips']} |")
            if not t["enrolled_chips"]:
                lines.append(head + " – | – | – | – | – | – | – | – | – |")
                continue
            n = t["reconstructions"]
            lines.append(
                head + f" {fmt_ber(t['key_reliability_single'], n * 216 * 3)} | "
                f"{fmt_ber(t['key_reliability_majority'], n * 216)} | "
                f"{fmt_rate(t['detected_failures'], n)} | "
                f"{fmt_rate(t['silent_wrong_keys'], n)} | "
                f"{t['needed_remeasure'] / n:.2%} | "
                f"{t['chips_with_failures']}/{t['enrolled_chips']} | "
                f"{t['worst_chip_failures']}/{n // t['enrolled_chips']} | "
                f"{t['key_uniqueness']:.2%} | "
                f"{t['distinct_ids']}/{t['enrolled_chips']} |")
    lines += [
        "",
        "## Effect of the key-check value (model)",
        "",
        "Same chips, enrollment and measurement noise streams with and without",
        "the KCV (paired). \"Caught\" = reconstructions where the KCV rejected a",
        "SECDED miscorrection at least once; \"recovered\" = of those, ended with",
        "the right key after re-measurement.",
        "",
        "| σ_process | τ (counts) | without KCV: detected | without KCV: silent wrong key | "
        "with KCV: detected | with KCV: silent wrong key | KCV caught | caught & recovered |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for t in r["per_tau"]:
            if not t["enrolled_chips"]:
                continue
            o, n = t["without_kcv"], t["reconstructions"]
            lines.append(
                f"| {r['sigma_process']:.1%} | {t['tau']} | "
                f"{fmt_rate(o['detected_failures'], n)} | "
                f"{fmt_rate(o['silent_wrong_keys'], n)} | "
                f"{fmt_rate(t['detected_failures'], n)} | "
                f"{fmt_rate(t['silent_wrong_keys'], n)} | "
                f"{t['kcv_caught']} | {t['kcv_caught_recovered']} |")
    lines += [
        "",
        "## Enrollment temperatures (model)",
        "",
        "A pair passes when its mean Δ has the same sign at every enrollment",
        "temperature and |mean Δ| ≥ τ at each of them. 16 measurements per",
        "temperature. KCV on; reconstruction streams identical across modes.",
        "",
        "| σ_process | τ (counts) | enrollment at | pairs passing mean [min–max] | "
        "chips failing enrollment | key BER 1 meas | key BER maj-3 | "
        "detected failure rate | silent wrong-key rate | chips with ≥1 failure | "
        "worst chip failures / recon |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        for t in r["per_tau"]:
            for label, m in t["enroll_modes"].items():
                head = (f"| {r['sigma_process']:.1%} | {t['tau']} | {label} | "
                        f"{m['pairs_passing_mean']:.0f} [{m['pairs_passing_min']}–"
                        f"{m['pairs_passing_max']}] | {m['enroll_failed_chips']}/"
                        f"{m['enroll_failed_chips'] + m['enrolled_chips']} |")
                if not m["enrolled_chips"]:
                    lines.append(head + " – | – | – | – | – | – |")
                    continue
                n = m["reconstructions"]
                lines.append(
                    head + f" {fmt_ber(m['key_reliability_single'], n * 216 * 3)} | "
                    f"{fmt_ber(m['key_reliability_majority'], n * 216)} | "
                    f"{fmt_rate(m['detected_failures'], n)} | "
                    f"{fmt_rate(m['silent_wrong_keys'], n)} | "
                    f"{m['chips_with_failures']}/{m['enrolled_chips']} | "
                    f"{m['worst_chip_failures']}/{n // m['enrolled_chips']} |")
    (out / "results.md").write_text("\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=Path("docs/puf-model"))
    ap.add_argument("--chips", type=int, default=100)
    ap.add_argument("--recon", type=int, default=1000)
    ap.add_argument("--raw", type=int, default=20,
                    help="raw single measurements per chip per temperature")
    ap.add_argument("--seed", type=int, default=20261003)
    ap.add_argument("--jobs", type=int, default=len(SIGMAS))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(run_sigma, SIGMAS, [args.chips] * len(SIGMAS),
                              [args.recon] * len(SIGMAS), [args.raw] * len(SIGMAS),
                              [args.seed] * len(SIGMAS)))
    meta = {
        "label": "model",
        "chips": args.chips, "reconstructions_per_chip": args.recon,
        "raw_measurements_per_temp": args.raw, "seed": args.seed,
        "sigmas": SIGMAS, "taus": TAUS, "temp_range_c": TEMP_RANGE_C,
        "enroll_modes": {label: list(t) for label, t in ENROLL_MODES},
        "keygen": asdict(rp.KeyGenParams()),
        "numpy": np.__version__, "python": platform.python_version(),
    }
    saved = [{k: v for k, v in r.items() if k != "hist"} for r in results]
    (args.out / "results.json").write_text(
        json.dumps({"meta": meta, "results": saved}, indent=1) + "\n")
    write_tables(results, args.out)

    import puf_plots
    puf_plots.plot_all(results, args.out)
    print(f"done in {time.time() - t0:.1f} s -> {args.out}")


if __name__ == "__main__":
    main()
