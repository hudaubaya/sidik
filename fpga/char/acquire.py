# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Acquire RO-pair race measurements into a characterization run directory.

    python3 fpga/char/acquire.py --backend sim --out fpga/char/runs/sim-demo

A run directory holds:

  deltas.csv  one row per race: chip,temp_c,vdd_v,rep,pair,delta
              delta = count(RO 2*pair) - count(RO 2*pair+1) when the first
              counter reaches the threshold; vdd_v is nan when not recorded.
  meta.json   source ("sim" or "hardware"), count_threshold, n_ro, and for
              hardware runs the board, bitstream hash and operator notes.

analyze.py reads exactly this format, so any acquisition tool (this script,
a logic analyser export, a vendor script) can feed it.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "model"))
import ro_puf as rp  # noqa: E402

HEADER = "chip,temp_c,vdd_v,rep,pair,delta"


class Backend:
    """Source of race measurements for one chip at a time."""

    source = "abstract"

    def chips(self):
        raise NotImplementedError

    def set_condition(self, chip, temp_c, vdd_v):
        """Bring `chip` to the condition (chamber setpoint, supply) and settle."""
        raise NotImplementedError

    def measure(self, chip, n_reps):
        """Return (n_reps, n_pairs) int deltas for every pair of `chip`."""
        raise NotImplementedError

    def meta(self):
        return {}


class HardwareBackend(Backend):
    """Placeholder for an automated chamber / supply setup.

    Board data is recorded with fpga/char/syscon/measure_pairs.tcl (System
    Console) and converted with `sw/analyze.py --export-run`; see
    docs/char_howto.md. Implement set_condition() and measure() here only to
    drive a temperature chamber from Python.
    """

    source = "hardware"

    def __init__(self, *_, **__):
        raise NotImplementedError(
            "No Python hardware backend: record races with "
            "fpga/char/syscon/measure_pairs.tcl and convert them with "
            "`sw/analyze.py --export-run` (docs/char_howto.md).")


@dataclass(frozen=True)
class SimExtras:
    # Spread of a per-RO quadratic temperature term (1/degC^2). 0 keeps the
    # model's linear tempco; non-zero lets analyze.py's linearity check be
    # exercised.
    sigma_tempco2: float = 0.0


class SimBackend(Backend):
    """Virtual chips from model/ro_puf.py (labelled as simulated data)."""

    source = "sim"

    def __init__(self, params: rp.PufParams, extras: SimExtras, n_chips, seed):
        self.params, self.extras = params, extras
        root = np.random.SeedSequence(seed)
        self._chip_ss, self._meas_ss = root.spawn(2)
        rngs = [np.random.default_rng(s) for s in self._chip_ss.spawn(n_chips)]
        self._chips = [rp.Chip(params, r) for r in rngs]
        self._k2 = [extras.sigma_tempco2 * r.standard_normal(params.n_ro) for r in rngs]
        self._rng = np.random.default_rng(self._meas_ss)
        self._cond = {}

    def chips(self):
        return list(range(len(self._chips)))

    def set_condition(self, chip, temp_c, vdd_v):
        self._cond[chip] = temp_c

    def measure(self, chip, n_reps):
        t = self._cond[chip]
        c = self._chips[chip]
        dt = t - self.params.t_ref_c
        f = c.freqs(t) * (1 + self._k2[chip] * dt * dt)
        fa, fb = f[0::2], f[1::2]
        return rp.race(fa, fb, self.params, self._rng, shape=(n_reps, fa.size))

    def meta(self):
        return {"true_params": asdict(self.params), "sim_extras": asdict(self.extras)}


def acquire(backend: Backend, temps_c, n_reps, out: Path, vdd_v=float("nan")):
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for chip in backend.chips():
        for t in temps_c:
            backend.set_condition(chip, t, vdd_v)
            d = np.asarray(backend.measure(chip, n_reps), dtype=np.int64)
            reps, pairs = np.meshgrid(np.arange(d.shape[0]), np.arange(d.shape[1]),
                                      indexing="ij")
            block = np.column_stack([
                np.full(d.size, chip), np.full(d.size, t), np.full(d.size, vdd_v),
                reps.ravel(), pairs.ravel(), d.ravel()])
            rows.append(block)
    data = np.vstack(rows)
    np.savetxt(out / "deltas.csv", data, delimiter=",", header=HEADER, comments="",
               fmt=["%d", "%g", "%g", "%d", "%d", "%d"])
    p = getattr(backend, "params", rp.PufParams())
    meta = {"source": backend.source, "count_threshold": p.count_threshold,
            "n_ro": p.n_ro, "temps_c": list(temps_c), "reps": n_reps,
            **backend.meta()}
    (out / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backend", choices=("sim", "hardware"), default="sim")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--chips", type=int, default=10)
    ap.add_argument("--temps", type=float, nargs="+", default=[-40, 0, 25, 50, 85])
    ap.add_argument("--reps", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--sigma-process", type=float, default=rp.PufParams.sigma_process)
    ap.add_argument("--sigma-jitter", type=float, default=rp.PufParams.sigma_jitter)
    ap.add_argument("--sigma-tempco", type=float, default=rp.PufParams.sigma_tempco)
    ap.add_argument("--sigma-tempco2", type=float, default=0.0,
                    help="sim only: per-RO quadratic tempco spread, 1/degC^2")
    args = ap.parse_args()
    if args.backend == "hardware":
        backend = HardwareBackend()
    else:
        params = rp.PufParams(sigma_process=args.sigma_process,
                              sigma_jitter=args.sigma_jitter,
                              sigma_tempco=args.sigma_tempco)
        backend = SimBackend(params, SimExtras(args.sigma_tempco2), args.chips, args.seed)
    out = acquire(backend, args.temps, args.reps, args.out)
    print(f"wrote {out}/deltas.csv and meta.json ({backend.source})")


if __name__ == "__main__":
    main()
