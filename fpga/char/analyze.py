# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Characterize RO-PUF race data and fit the model parameters.

    python3 fpga/char/analyze.py fpga/char/runs/<run>

Reads <run>/deltas.csv and <run>/meta.json (format: see acquire.py) and
writes into <run>/analysis/:

  fit.json    sigma_process, sigma_jitter, sigma_tempco estimated from the
              data (pooled and per chip) plus a linearity test; feed it to
              `python3 model/puf_montecarlo.py --params fit.json`.
  report.md   uniformity, uniqueness, reliability vs temperature, fitted
              parameters, linearity, and the figures.

Every race is converted to x = ln(f_a / f_b), which the counts give
directly: x = -ln(1 - delta'/N) when RO 2i wins, ln(1 + delta'/N) otherwise
(N = counter threshold, delta' = delta - b sign(delta) removes the
measurement's bias b on |delta|: +0.5 counts for the model's race(), about
-3.0 for rtl/ropuf; set it as delta_magnitude_bias in meta.json). In x the model is

  x(T) = x0 + s * u(T),  u(T) = (T - T_ref) / (1 + k0 (T - T_ref))

with s = k_a - k_b per pair and k0 the common tempco, fitted once for all
pairs. Estimators:
  per-race variance of x       = 2 sigma_jitter^2 + q
  variance over pairs of x0    = 2 sigma_process^2 + noise
  variance over pairs of s     = 2 sigma_tempco^2 + slope noise
q (counter phase quantisation) is computed from the model's race(). The
linearity check is the chi^2/dof of that fit; about 1 means the model's
linear tempco describes the data.
"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import ro_puf as rp  # noqa: E402

T_REF_C = 25.0
LINEARITY_P = 1e-3
Z_LINEARITY = 3.090  # one-sided normal quantile for p = 1e-3

LABELS = {"sim": "simulated", "hardware": "measured"}


class RunData:
    """Deltas as a dense array D[chip, temp, rep, pair] at one supply voltage."""

    def __init__(self, run: Path):
        self.run = Path(run)
        self.meta = json.loads((self.run / "meta.json").read_text())
        self.source = self.meta.get("source", "hardware")
        self.label = LABELS.get(self.source, self.source)
        self.n = int(self.meta["count_threshold"])
        self.bias = float(self.meta.get("delta_magnitude_bias", DEFAULT_DELTA_BIAS))
        a = np.loadtxt(self.run / "deltas.csv", delimiter=",", skiprows=1, ndmin=2)
        chip, temp, vdd, rep, pair, delta = a.T
        vdd = np.where(np.isnan(vdd), -1.0, vdd)
        vals, counts = np.unique(vdd, return_counts=True)
        self.vdd = float(vals[counts.argmax()])  # nominal = most common supply
        keep = vdd == self.vdd
        chip, temp, rep, pair, delta = (x[keep] for x in (chip, temp, rep, pair, delta))
        self.chips = np.unique(chip).astype(int)
        self.temps = np.unique(temp)
        n_rep = int(rep.max()) + 1
        n_pair = int(pair.max()) + 1
        ci = np.searchsorted(self.chips, chip)
        ti = np.searchsorted(self.temps, temp)
        d = np.full((self.chips.size, self.temps.size, n_rep, n_pair), np.nan)
        d[ci, ti, rep.astype(int), pair.astype(int)] = delta
        if np.isnan(d).any() or d.size != delta.size:
            raise ValueError("deltas.csv must hold every (chip, temp, rep, pair) "
                             "exactly once at the nominal supply")
        self.d = d
        self.kref = int(np.abs(self.temps - T_REF_C).argmin())

    @property
    def t_ref(self):
        return float(self.temps[self.kref])


DEFAULT_DELTA_BIAS = 0.5  # model race(): loser misses half a count on average


def to_x(delta, n, bias=DEFAULT_DELTA_BIAS):
    """Counts -> x = ln(f_a / f_b).

    `bias` is the mean excess of |delta| over n |1 - f_slow/f_fast| for the
    measurement circuit; it is removed first.
    """
    delta = np.asarray(delta, dtype=float)
    d = (delta - bias * np.sign(delta)) / n
    return np.where(d > 0, -np.log1p(-np.minimum(d, 1 - 1e-12)), np.log1p(d))


def quantisation_variance(n, rng=None):
    """Variance of x from counter phases alone (no jitter)."""
    rng = rng or np.random.default_rng(0)
    p = rp.PufParams(sigma_jitter=0.0, count_threshold=n)
    fa = 250e6 * (1 + 0.01 * rng.standard_normal(400))
    fb = 250e6 * (1 + 0.01 * rng.standard_normal(400))
    x = to_x(rp.race(fa, fb, p, rng, shape=(400, fa.size)), n)
    return float(x.var(axis=0, ddof=1).mean())


def _excess_sigma(total_var, noise_var):
    return math.sqrt(max(total_var - noise_var, 0.0) / 2)


def line_fit(xbar, s2, u, n_rep):
    """Fit xbar (temps, pairs) = a + s u; return slopes, chi2/dof per pair."""
    a = np.column_stack([np.ones_like(u), u])
    coef, *_ = np.linalg.lstsq(a, xbar, rcond=None)
    resid = xbar - a @ coef
    chi2 = ((resid ** 2) / (s2[:, None] / n_rep)).sum(axis=0) / (u.size - 2)
    return coef[1], chi2


def u_of(temps, t_ref, k0):
    dt = temps - t_ref
    return dt / (1 + k0 * dt)


def fit_k0(xbars, s2s, temps, t_ref, n_rep, grid=np.linspace(-4e-3, 4e-3, 161)):
    """Common tempco minimising the total chi2 over all chips and pairs."""
    if temps.size < 3:
        return 0.0
    tot = [sum(line_fit(xb, s2, u_of(temps, t_ref, k), n_rep)[1].sum()
               for xb, s2 in zip(xbars, s2s)) for k in grid]
    return float(grid[int(np.argmin(tot))])


def fit_chip(x, temps, kref, t_ref, k0, q):
    """x: (temps, reps, pairs) for one chip -> parameter estimates."""
    n_rep = x.shape[1]
    xbar = x.mean(axis=1)                       # (temps, pairs)
    s2 = x.var(axis=1, ddof=1).mean(axis=1)     # (temps,) pooled over pairs
    out = {
        "sigma_jitter": _excess_sigma(float(s2.mean()), q),
        "sigma_process": _excess_sigma(float(xbar[kref].var(ddof=1)),
                                       float(s2[kref]) / n_rep),
    }
    if temps.size >= 2:
        u = u_of(temps, t_ref, k0)
        if temps.size >= 3:
            slope, chi2 = line_fit(xbar, s2, u, n_rep)
            out["chi2_dof"] = chi2
        else:
            slope = (xbar[1] - xbar[0]) / (u[1] - u[0])
        uc = u - u.mean()
        slope_noise = float((s2 / n_rep).mean()) / float((uc ** 2).sum())
        out["sigma_tempco"] = _excess_sigma(float(slope.var(ddof=1)), slope_noise)
        out["slopes"] = slope
    return out


def chi2_dof_threshold(dof, z=Z_LINEARITY):
    """Wilson-Hilferty approximation to the upper chi2/dof quantile."""
    a = 2 / (9 * dof)
    return (1 - a + z * math.sqrt(a)) ** 3


def analyze(run: RunData):
    n, temps, kref = run.n, run.temps, run.kref
    q = quantisation_variance(n)
    xs = [to_x(run.d[c], n, run.bias) for c in range(run.chips.size)]
    k0 = fit_k0([x.mean(axis=1) for x in xs],
                [x.var(axis=1, ddof=1).mean(axis=1) for x in xs],
                temps, run.t_ref, run.d.shape[2])
    per_chip = [fit_chip(x, temps, kref, run.t_ref, k0, q) for x in xs]

    ref = run.d[:, kref].mean(axis=1) > 0            # (chips, pairs)
    bits = run.d > 0                                  # (chips, temps, reps, pairs)
    ber = (bits != ref[:, None, None, :]).mean(axis=(2, 3))  # (chips, temps)
    mean_sign = run.d.mean(axis=2) > 0
    flips = (mean_sign != mean_sign[:, :1]).any(axis=1).mean(axis=1)

    def pooled(key):
        v = [c[key] for c in per_chip if key in c]
        return (float(np.sqrt(np.mean(np.square(v)))), float(np.std(v))) if v else None

    result = {
        "label": run.label,
        "source": run.source,
        "count_threshold": n,
        "n_ro": int(run.meta.get("n_ro", 2 * run.d.shape[3])),
        "chips": int(run.chips.size),
        "temps_c": temps.tolist(),
        "reps": int(run.d.shape[2]),
        "t_ref_c": run.t_ref,
        "vdd_v": None if run.vdd < 0 else run.vdd,
        "quantisation_variance_x": q,
        "delta_magnitude_bias": run.bias,
        "tempco_common_fit": k0 if temps.size >= 3 else None,
        "uniformity_mean": float(ref.mean()),
        "uniformity_per_chip": ref.mean(axis=1).tolist(),
        "reliability_vs_temp": {f"{t:g}": float(1 - ber[:, k].mean())
                                for k, t in enumerate(temps)},
        "reliability_worst_chip_vs_temp": {f"{t:g}": float(1 - ber[:, k].max())
                                           for k, t in enumerate(temps)},
        "pairs_flipping_over_range": float(flips.mean()),
        "per_chip": [{k: v for k, v in c.items() if k not in ("slopes", "chi2_dof")}
                     for c in per_chip],
    }
    for key in ("sigma_process", "sigma_jitter", "sigma_tempco"):
        p = pooled(key)
        if p:
            result[key], result[key + "_chip_std"] = p
    if run.chips.size >= 2:
        b = ref.astype(np.int64)
        diff = b @ (1 - b).T + (1 - b) @ b.T
        iu = np.triu_indices(b.shape[0], k=1)
        hd = diff[iu] / b.shape[1]
        result["uniqueness_mean"] = float(hd.mean())
        result["uniqueness_std"] = float(hd.std())
    if temps.size >= 3:
        chi2 = np.concatenate([c["chi2_dof"] for c in per_chip])
        thr = chi2_dof_threshold(temps.size - 2)
        result["linearity"] = {
            "chi2_dof_median": float(np.median(chi2)),
            "chi2_dof_mean": float(chi2.mean()),
            "threshold_p": LINEARITY_P,
            "chi2_dof_threshold": thr,
            "fraction_nonlinear": float((chi2 > thr).mean()),
            "expected_fraction_if_linear": LINEARITY_P,
        }
    arrays = {
        "mean_ref": run.d[:, kref].mean(axis=1),
        "ber": ber,
        "slopes": (np.concatenate([c["slopes"] for c in per_chip])
                   if temps.size >= 2 else None),
        "chi2": np.concatenate([c["chi2_dof"] for c in per_chip]) if temps.size >= 3 else None,
    }
    return result, arrays


def write_report(result, arrays, temps, out: Path):
    lab = result["label"]
    lines = [
        f"# RO-PUF characterization ({lab})",
        "",
        f"**Data: {lab}** (source `{result['source']}`). "
        + ("These numbers come from the behavioural model, not from hardware."
           if result["source"] == "sim" else
           "Record board, bitstream hash and conditions in meta.json."),
        "",
        f"{result['chips']} chips, temperatures {', '.join(f'{t:g}' for t in temps)} °C, "
        f"{result['reps']} races per pair per temperature, counter threshold "
        f"{result['count_threshold']}, reference {result['t_ref_c']:g} °C.",
        "",
        f"## Fitted model parameters ({lab})",
        "",
        "| Parameter | Pooled | Std across chips |",
        "|---|---|---|",
    ]
    for key in ("sigma_process", "sigma_jitter", "sigma_tempco"):
        if key in result:
            lines.append(f"| {key} | {result[key]:.3e} | {result[key + '_chip_std']:.1e} |")
    if result.get("tempco_common_fit") is not None:
        lines += ["", f"Common tempco from the curvature of all pairs: "
                  f"{result['tempco_common_fit']:.1e} /°C. Weakly determined: it "
                  "only enters at second order."]
    if "linearity" in result:
        li = result["linearity"]
        lines += [
            "",
            f"## Linearity of the tempco ({lab})",
            "",
            "χ²/dof of each pair's fit of x = ln(f_a/f_b) to the model's linear "
            "tempco, x = x0 + s·ΔT/(1 + k0·ΔT), with the measured jitter as the "
            "noise. ≈ 1 means the linear tempco of the model fits; the two-corner "
            "enrollment result in docs/puf-model.md depends on it.",
            "",
            f"- median χ²/dof: {li['chi2_dof_median']:.2f}, mean {li['chi2_dof_mean']:.2f}",
            f"- pairs above the p = {li['threshold_p']:g} threshold "
            f"(χ²/dof > {li['chi2_dof_threshold']:.2f}): "
            f"{li['fraction_nonlinear']:.2%} (expected {li['expected_fraction_if_linear']:.2%} "
            "if linear)",
        ]
    lines += [
        "",
        f"## Response quality ({lab})",
        "",
        f"- uniformity: {result['uniformity_mean']:.2%}",
    ]
    if "uniqueness_mean" in result:
        lines.append(f"- uniqueness: {result['uniqueness_mean']:.2%} ± "
                     f"{result['uniqueness_std']:.2%}")
    lines += [
        f"- pairs whose mean sign changes over the temperature range: "
        f"{result['pairs_flipping_over_range']:.2%}",
        "",
        "| temperature (°C) | reliability, mean over chips | worst chip at that temperature |",
        "|---|---|---|",
    ]
    for t in temps:
        k = f"{t:g}"
        lines.append(f"| {k} | {result['reliability_vs_temp'][k]:.2%} | "
                     f"{result['reliability_worst_chip_vs_temp'][k]:.2%} |")
    lines += ["", "![Mean Δ distribution](delta_hist.png)",
              "![Reliability vs temperature](reliability_vs_temp.png)"]
    if arrays["slopes"] is not None:
        lines.append("![Tempco slopes](tempco_slopes.png)")
    if arrays["chi2"] is not None:
        lines.append("![Linearity](linearity.png)")
    (out / "report.md").write_text("\n".join(lines) + "\n")


def plot(result, arrays, temps, out: Path):
    import puf_plots as pp
    plt = pp.plt
    lab = result["label"]
    note = f"{lab.upper()} data" + (" (model)" if result["source"] == "sim" else "")

    fig, ax = plt.subplots(figsize=(7, 4.2))
    m = arrays["mean_ref"].ravel()
    ax.hist(m, bins=80, density=True, color=pp.SERIES[0], edgecolor=pp.SURFACE,
            linewidth=0.5)
    ax.set_xlabel(f"mean Δ at {result['t_ref_c']:g} °C (counts)")
    ax.set_ylabel("density")
    pp._finish(fig, "Mean Δ over all pairs and chips", out / "delta_hist.png",
               label=lab, note=note)

    fig, ax = plt.subplots(figsize=(7, 4.2))
    ber = arrays["ber"]
    ax.plot(temps, 100 * (1 - ber.mean(axis=0)), color=pp.SERIES[0], marker="o",
            label="mean over chips")
    ax.plot(temps, 100 * (1 - ber.max(axis=0)), color=pp.SERIES[1], marker="o",
            label="worst chip at each temperature")
    ax.set_xlabel("temperature (°C)")
    ax.set_ylabel("reliability, 1 measurement (%)")
    ax.legend(loc="lower center")
    pp._finish(fig, "Raw reliability vs temperature", out / "reliability_vs_temp.png",
               label=lab, note=note)

    if arrays["slopes"] is not None:
        fig, ax = plt.subplots(figsize=(7, 4.2))
        ax.hist(1e6 * arrays["slopes"], bins=80, density=True, color=pp.SERIES[2],
                edgecolor=pp.SURFACE, linewidth=0.5)
        ax.set_xlabel("tempco difference k_a − k_b per pair (ppm/°C)")
        ax.set_ylabel("density")
        pp._finish(fig, "Per-pair tempco difference", out / "tempco_slopes.png",
                   label=lab, note=note)

    if arrays["chi2"] is not None:
        fig, ax = plt.subplots(figsize=(7, 4.2))
        chi2 = arrays["chi2"]
        hi = max(8.0, float(np.percentile(chi2, 99.5)))
        ax.hist(np.clip(chi2, 0, hi), bins=80, density=True, color=pp.SERIES[0],
                edgecolor=pp.SURFACE, linewidth=0.5)
        thr = result["linearity"]["chi2_dof_threshold"]
        ax.axvline(thr, color=pp.INK_2, linestyle="--", linewidth=1)
        ax.annotate(f"p = {LINEARITY_P:g}", (thr, ax.get_ylim()[1]), xytext=(4, -4),
                    textcoords="offset points", va="top", color=pp.INK_2, fontsize=9)
        ax.set_xlabel("χ²/dof of the linear-tempco fit per pair")
        ax.set_ylabel("density")
        pp._finish(fig, "Linear-tempco fit quality", out / "linearity.png",
                   label=lab, note=note)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run", type=Path, help="run directory with deltas.csv and meta.json")
    ap.add_argument("--out", type=Path, help="default: <run>/analysis")
    args = ap.parse_args()
    run = RunData(args.run)
    out = args.out or args.run / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    result, arrays = analyze(run)
    (out / "fit.json").write_text(json.dumps(result, indent=1) + "\n")
    write_report(result, arrays, run.temps, out)
    plot(result, arrays, run.temps, out)
    print(f"{result['label']} data: sigma_process={result['sigma_process']:.3e} "
          f"sigma_jitter={result['sigma_jitter']:.3e} "
          f"sigma_tempco={result.get('sigma_tempco', float('nan')):.3e} -> {out}")


if __name__ == "__main__":
    main()
