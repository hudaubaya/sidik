# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Figures for puf_montecarlo.py. Every figure is labelled as model output."""

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES = ("#2a78d6", "#eb6834", "#1baf7a")  # categorical slots 1-3
MODEL_NOTE = "MODEL output, not measured"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "text.color": INK,
    "axes.labelcolor": INK_2, "axes.edgecolor": GRID,
    "xtick.color": INK_2, "ytick.color": INK_2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.size": 10, "axes.titlesize": 11, "legend.frameon": False,
    "lines.linewidth": 2, "lines.markersize": 7,
})


def _finish(fig, title, path):
    fig.suptitle(f"{title} (model)", x=0.01, ha="left", fontsize=12)
    fig.text(0.99, 0.01, MODEL_NOTE, ha="right", va="bottom",
             color=INK_2, fontsize=8)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def _sigma_label(r):
    return f"σ_process {r['sigma_process']:.1%}"


def _direct_label(ax, x, y, text, color):
    ax.annotate(text, (x, y), xytext=(6, 0), textcoords="offset points",
                va="center", color=INK, fontsize=9)


def plot_pairs(results, out):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for r, c in zip(results, SERIES):
        t = [x["tau"] for x in r["per_tau"]]
        mean = np.array([x["pairs_passing_mean"] for x in r["per_tau"]])
        lo = mean - [x["pairs_passing_min"] for x in r["per_tau"]]
        hi = [x["pairs_passing_max"] for x in r["per_tau"]] - mean
        ax.errorbar(t, mean, yerr=[lo, hi], color=c, marker="o", capsize=3,
                    label=_sigma_label(r))
        _direct_label(ax, t[-1], mean[-1], f"{r['sigma_process']:.1%}", c)
    ax.axhline(216, color=INK_2, linestyle="--", linewidth=1)
    ax.annotate("216 key bits needed", (0, 216), xytext=(2, 4),
                textcoords="offset points", color=INK_2, fontsize=9)
    ax.set_xlabel("mask threshold τ (counts)")
    ax.set_ylabel("pairs with |Δ̄| ≥ τ (of 512)")
    ax.set_ylim(0, 540)
    ax.legend(loc="lower left")
    _finish(fig, "Pairs passing the mask: mean, min–max over chips",
            out / "pairs_passing.png")


def _rate_panel(ax, results, key, title, variant=None):
    # Zero-event markers share one bound per tau; offset them per series.
    offsets = np.linspace(-2.5, 2.5, len(results))
    for r, c, dx in zip(results, SERIES, offsets):
        xs, ys, zx, zy = [], [], [], []
        for x in r["per_tau"]:
            if not x["enrolled_chips"]:
                continue
            k, n = (x[variant] if variant else x)[key], x["reconstructions"]
            if k:
                xs.append(x["tau"])
                ys.append(k / n)
            else:
                zx.append(x["tau"] + dx)
                zy.append(x["zero_event_bound"])
        ax.plot(xs, ys, color=c, marker="o", label=_sigma_label(r))
        ax.plot(zx, zy, linestyle="none", marker="v", markerfacecolor=SURFACE,
                markeredgecolor=c, markeredgewidth=1.5)
    ax.set_yscale("log")
    ax.set_xlabel("mask threshold τ (counts)")
    ax.set_title(title, loc="left", color=INK)


def plot_failures(results, out):
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4), sharey=True)
    _rate_panel(axes[0], results, "detected_failures",
                "No KCV: detected failure", "without_kcv")
    _rate_panel(axes[1], results, "silent_wrong_keys",
                "No KCV: silent wrong key", "without_kcv")
    _rate_panel(axes[2], results, "detected_failures",
                "32-bit KCV: detected failure\n(silent wrong key: 0 observed in every case)")
    axes[0].set_ylabel("rate per reconstruction")
    axes[0].plot([], [], linestyle="none", marker="v", markerfacecolor=SURFACE,
                 markeredgecolor=INK_2, label="0 observed: 95 % upper bound")
    axes[0].legend(loc="lower left", fontsize=8)
    _finish(fig, "Key reconstruction failures, T ~ U(−40, 85) °C",
            out / "failure_rate.png")


def plot_reliability(results, out):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for r, c in zip(results, SERIES):
        rel = r["raw"]["reliability_vs_temp"]
        t = sorted(rel, key=float)
        y = [100 * rel[k] for k in t]
        ax.plot([float(k) for k in t], y, color=c, marker="o", label=_sigma_label(r))
        _direct_label(ax, float(t[-1]), y[-1], f"{r['sigma_process']:.1%}", c)
    ax.set_ylim(top=100.5)
    ax.axvline(25, color=INK_2, linestyle=":", linewidth=1)
    ax.annotate("enrollment 25 °C", (25, 100.5), xytext=(4, -2),
                textcoords="offset points", va="top", color=INK_2, fontsize=9)
    ax.set_xlabel("temperature (°C)")
    ax.set_ylabel("raw reliability, 1 measurement, 512 bits (%)")
    ax.legend(loc="lower center")
    _finish(fig, "Raw response reliability vs temperature", out / "reliability_vs_temp.png")


def plot_key_ber(results, out):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2), sharey=True)
    offsets = np.linspace(-2.5, 2.5, len(results))
    for ax, key, title, n_meas in (
            (axes[0], "key_reliability_single", "1 measurement", 3),
            (axes[1], "key_reliability_majority", "majority of 3", 1)):
        for r, c, dx in zip(results, SERIES, offsets):
            rows = [x for x in r["per_tau"] if x["enrolled_chips"]]
            nz = [(x["tau"], 1 - x[key]) for x in rows if 1 - x[key] > 0]
            zero = [(x["tau"] + dx, 3 / (x["reconstructions"] * 216 * n_meas))
                    for x in rows if 1 - x[key] <= 0]
            if nz:
                ax.plot(*zip(*nz), color=c, marker="o", label=_sigma_label(r))
            if zero:
                ax.plot(*zip(*zero), linestyle="none", marker="v",
                        markerfacecolor=SURFACE, markeredgecolor=c,
                        markeredgewidth=1.5)
        ax.set_yscale("log")
        ax.set_xlabel("mask threshold τ (counts)")
        ax.set_title(f"Selected-bit error rate, {title}", loc="left", color=INK)
    axes[0].set_ylabel("bit error rate before ECC")
    axes[0].plot([], [], linestyle="none", marker="v", markerfacecolor=SURFACE,
                 markeredgecolor=INK_2, label="0 observed: 95 % upper bound")
    axes[0].legend(loc="lower left", fontsize=8)
    _finish(fig, "Effect of the mask on the 216 key bits, T ~ U(−40, 85) °C",
            out / "key_ber.png")


def plot_hd(results, out, sigma=0.01):
    r = next(x for x in results if abs(x["sigma_process"] - sigma) < 1e-12)
    h = r["hist"]
    fig, ax = plt.subplots(figsize=(7, 4.2))
    bins = np.linspace(0, 0.6, 121)
    for data, c, label in ((h["intra_25"], SERIES[1], "intra-chip, 25 °C"),
                           (h["intra_worst"], SERIES[2], "intra-chip, worse of −40/85 °C"),
                           (h["inter"], SERIES[0], "inter-chip")):
        ax.hist(np.asarray(data), bins=bins, density=True, color=c, alpha=0.85,
                label=label, edgecolor=SURFACE, linewidth=0.5)
    ax.set_xlabel("fractional Hamming distance, raw 512-bit response")
    ax.set_ylabel("density")
    ax.legend(loc="upper center")
    _finish(fig, f"Inter- vs intra-chip distance, σ_process {sigma:.0%}",
            out / "hd_hist.png")


def plot_enroll_modes(results, out):
    labels = list(results[0]["per_tau"][0]["enroll_modes"])
    fig, axes = plt.subplots(2, len(results), figsize=(13, 7.6), sharey="row")
    offsets = np.linspace(-2.5, 2.5, len(labels))
    for col, r in enumerate(results):
        top, bottom = axes[0, col], axes[1, col]
        for label, c, dx in zip(labels, SERIES, offsets):
            rows = [(t["tau"], t["enroll_modes"][label]) for t in r["per_tau"]]
            top.plot([t for t, _ in rows], [m["pairs_passing_mean"] for _, m in rows],
                     color=c, marker="o", label=f"enrolled at {label}")
            xs, ys, zx, zy = [], [], [], []
            for t, m in rows:
                if not m["enrolled_chips"]:
                    continue
                k = m["detected_failures"] + m["silent_wrong_keys"]
                if k:
                    xs.append(t)
                    ys.append(k / m["reconstructions"])
                else:
                    zx.append(t + dx)
                    zy.append(m["zero_event_bound"])
            bottom.plot(xs, ys, color=c, marker="o")
            bottom.plot(zx, zy, linestyle="none", marker="v", markerfacecolor=SURFACE,
                        markeredgecolor=c, markeredgewidth=1.5)
        top.axhline(216, color=INK_2, linestyle="--", linewidth=1)
        top.set_title(_sigma_label(r), loc="left", color=INK)
        top.set_ylim(0, 540)
        bottom.set_yscale("log")
        bottom.set_xlabel("mask threshold τ (counts)")
        for ax in (top, bottom):
            ax.set_xlim(-6, 134)
    axes[0, 0].annotate("216 needed", (0, 216), xytext=(2, 4),
                        textcoords="offset points", color=INK_2, fontsize=9)
    axes[0, 0].set_ylabel("pairs passing (mean of 512)")
    axes[1, 0].set_ylabel("failure rate per reconstruction")
    axes[1, 0].plot([], [], linestyle="none", marker="v", markerfacecolor=SURFACE,
                    markeredgecolor=INK_2, label="0 observed: 95 % upper bound")
    handles = axes[0, 0].get_legend_handles_labels()
    extra = axes[1, 0].get_legend_handles_labels()
    axes[0, 0].legend(handles[0] + extra[0], handles[1] + extra[1],
                      loc="lower left", fontsize=8)
    _finish(fig, "Enrollment at one vs two temperatures, KCV on, T ~ U(−40, 85) °C",
            out / "enroll_modes.png")


def plot_all(results, out):
    plot_pairs(results, out)
    plot_failures(results, out)
    plot_reliability(results, out)
    plot_key_ber(results, out)
    plot_hd(results, out)
    plot_enroll_modes(results, out)
