# FPGA characterization

Scripts that turn RO-pair race measurements into the parameters of the PUF
model (`model/ro_puf.py`) and into response-quality metrics.

> **Status:** SIDIK has no RO-PUF RTL or register map yet, so there is no
> hardware acquisition backend. Everything below runs end to end on
> **simulated** data from the model, and every report says so. Hardware data
> can be fed in now if another tool writes the run format below.

## Workflow

```sh
# 1. acquire (simulated here; --backend hardware is a stub)
python3 fpga/char/acquire.py --backend sim --out fpga/char/runs/sim-demo

# 2. analyze: fit sigma_process / sigma_jitter / sigma_tempco, test linearity
python3 fpga/char/analyze.py fpga/char/runs/sim-demo
#    -> fpga/char/runs/sim-demo/analysis/{fit.json, report.md, *.png}

# 3. rerun the key-generator Monte Carlo with the fitted parameters
python3 model/puf_montecarlo.py --params fpga/char/runs/sim-demo/analysis/fit.json \
    --out fpga/char/runs/sim-demo/montecarlo
```

`make test-char` runs the checks (also part of `make test`).

## Run format

One directory per run:

| File | Contents |
|---|---|
| `deltas.csv` | Header `chip,temp_c,vdd_v,rep,pair,delta`; one row per race. `delta = count(RO 2·pair) − count(RO 2·pair+1)` when the first counter reaches the threshold. `vdd_v` is `nan` if not recorded. Every (chip, temperature, rep, pair) must be present exactly once at the nominal supply. |
| `meta.json` | `source` (`"sim"` or `"hardware"`), `count_threshold`, `n_ro`. For hardware runs also record board, bitstream hash, chamber, supply and operator notes. |

Measured runs belong in `fpga/char/runs/<date>-<board>/` and should be
committed together with their `meta.json`. `runs/sim-*` is git-ignored.

## What analyze.py estimates

Each race is converted to x = ln(f_a/f_b), which the counts give directly,
after removing the half count the losing counter misses on average. In x the
model is x(T) = x0 + s·ΔT/(1 + k0·ΔT), with s = k_a − k_b per pair and k0 the
common tempco.

| Output | From |
|---|---|
| `sigma_jitter` | per-race variance of x, minus counter-phase quantisation |
| `sigma_process` | spread of x0 over pairs at the reference temperature (nearest to 25 °C), minus averaging noise |
| `sigma_tempco` | spread of s over pairs, minus slope noise |
| `tempco_common_fit` | k0 minimising the total χ² over all pairs; weakly determined (second-order effect) |
| `linearity` | χ²/dof of each pair's fit. About 1 with ~0.1 % of pairs above the p = 0.001 line means the model's linear tempco fits. A large excess means it does not, and then the two-corner enrollment result in `docs/puf-model.md` should not be trusted. |
| reliability, uniformity, uniqueness | single-race bits against the reference-temperature mean sign |

Validated on simulated data (`test_char.py`): from 6 chips × 3 temperatures ×
20 races the fit recovers σ_process within 10 %, σ_jitter within 5 % and
σ_tempco within 15 %. A per-RO quadratic tempco spread of 1·10⁻⁷ /°C² gets
more than 20 % of pairs flagged as non-linear.

## Measurement plan (recommended)

- **Temperatures:** at least 3, including both corners of the intended range
  (e.g. −40, 25, 85 °C; 5 points is better). Two temperatures fit
  σ_tempco but cannot test linearity.
- **Repeats:** ≥ 20 races per pair per temperature (σ_jitter needs them).
- **Boards:** as many as available. σ_process and σ_tempco are averages over
  pairs within a chip, but uniqueness and chip-to-chip spread need several
  chips.
- **Supply:** log `vdd_v`. Only the most common supply value is analysed now.
  Supply dependence is not in the model yet.
- **Settling:** wait for the die temperature to settle at each setpoint
  before measuring. Record the actual die temperature if the FPGA has a
  sensor.

## Adding hardware

Implement `HardwareBackend` in `acquire.py`:
- `set_condition(chip, temp_c, vdd_v)` sets the chamber or supply and waits.
- `measure(chip, n_reps)` returns an `(n_reps, n_pairs)` array of deltas
  read from the board.

This needs the RO-PUF RTL and its register map in `rtl/` first.
