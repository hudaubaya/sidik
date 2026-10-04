# RO-PUF measurement core

Ring-oscillator PUF for FPGA characterization: 1024 ROs in disjoint pairs
(2i, 2i+1), measured by racing two counters to 2^14, behind an Avalon-MM
register interface.

> **Verified in simulation and generic synthesis only.** The ROs are
> replaced by a behavioural model in simulation, so the tests prove the
> measurement logic, registers and CDC, not RO behaviour on silicon. Nothing
> here has run on an FPGA yet.

> **Security: this is a characterization interface.** `DELTA`, `COUNT_A`
> and `COUNT_B` expose raw PUF responses on the bus. Software that can read
> them can recompute the key. A key-generation build must not expose raw
> responses: run enrollment and reconstruction in hardware or in a trusted
> context, and remove or lock these registers.

| File | Contents |
|---|---|
| `../ro_cell.v` | One RO: NAND enable + (N_STAGES−1) inverters. Generic path: each stage a preserved `ro_stage` instance. `CYCLONEV`: LUT + `lcell` primitive per stage. `SIM`: behavioural oscillator, half-period from `HALF_PERIOD_NS` or from `+RO_SEED`. |
| `../ro_array.v` | 1024 ROs in pairs; only the selected pair is enabled. Under `SIM` the pair is selected through OR trees of scalar nets instead of the 1024-bit mux (same function, since unselected ROs output 0; about 4× faster in Icarus). |
| `../puf_meas.v` | Two racing counters behind a ripple prescaler, control FSM, sign and magnitude of Δ in the 50 MHz domain. |
| `ropuf_core.v` | Latches the pair; `ro_array` + `puf_meas`. |
| `ropuf_avmm.v` | Avalon-MM slave (register map below). |
| `synth_check.py` | yosys generic synthesis; fails if any RO lost a stage. |

Parameters: `N_RO` (1024, even), `N_STAGES` (5, odd), `LOG2N` (14),
`PRESCALE_LOG2` (1), `TIMEOUT_RESET` (2^20 clk cycles).

## How a measurement works

1. **CLEAR:** counters held in asynchronous reset, all ROs off.
2. **ARM:** reset released while the ROs are still off (no clock edges, so
   no recovery or removal hazard).
3. **RUN:** only ROs 2·pair and 2·pair+1 are enabled. The other 1022 stay
   off, which reduces power and coupling. Each RO clocks only a ripple
   prescaler (one toggle flip-flop per stage, `PRESCALE_LOG2` = 1). The
   counter runs at f_RO/2, so it needs half the fmax of a counter clocked by
   the RO directly. The faster counter stops at exactly 2^14 RO cycles. Its
   `done` reaches the slower counter through two flops in the slower
   counter's divided clock domain, and the slower counter then stops.
4. **STOP:** once both `halted` flags are seen in `clk` after 2-flop
   synchronizers, the ROs are switched off and the counts are captured.
   The counts no longer change once halted, so the multi-bit capture is
   safe.
5. If RUN exceeds `TIMEOUT` clk cycles, the measurement ends with TIMEOUT
   set. The captured counts are then diagnostic only.

`DELTA = COUNT_A − COUNT_B` in RO cycles: positive when RO 2·pair is
faster. Counts are multiples of 2 (the prescaler), so Δ has a resolution of
2 RO cycles. The added quantisation noise (≈ 0.7 counts²) is small next to
the jitter noise the model assumes (≈ 48 counts²).

**Dead zone.** The slower counter runs 2 more divided cycles (4 RO cycles)
after the winner reaches 2^14. When the ideal |Δ| = 2^14·|1 − f_slow/f_fast|
is at most 4 counts, both counters therefore reach 2^14 and the pair reads
as Δ = 0 (bit 0). Outside that zone the sign is always right (checked on
200,000 random period pairs with `tb/common/ro_sim.py`). Any mask threshold
τ ≥ 16 counts drops these pairs anyway.

**Measurement bias.** For the same reason |DELTA| is on average **3.0
counts smaller** than the ideal |Δ| (−4.1 to −2.0). `fpga/char/analyze.py`
corrects for this when the run's `meta.json` sets `delta_magnitude_bias`
to −3.0.

**Duration.** About 2^14 / f_RO + a few clk cycles, i.e. ~65 µs at
250 MHz. A full 512-pair response takes ~34 ms plus bus overhead.

## Register map

Avalon-MM slave, 32-bit, word addresses (`avs_address[2:0]`), fixed read
latency 1, no waitrequest. Offsets in bytes:

| Offset | Name | Access | Contents |
|---|---|---|---|
| 0x00 | ID | R | `0x50554631` ("PUF1") |
| 0x04 | PARAMS | R | [15:0] pairs, [23:16] LOG2N, [31:24] RO stages |
| 0x08 | CTRL | W | bit0 START (ignored while busy) |
|      |      | R | bit0 BUSY, bit1 DONE, bit2 TIMEOUT |
| 0x0C | PAIR | RW | pair index for the next START |
| 0x10 | COUNT_A | R | counter of RO 2·pair |
| 0x14 | COUNT_B | R | counter of RO 2·pair+1 |
| 0x18 | DELTA | R | COUNT_A − COUNT_B, signed 32-bit |
| 0x1C | TIMEOUT | RW | RUN timeout, clk cycles |

To measure one pair: write PAIR, write CTRL = 1, poll CTRL until BUSY = 0,
check DONE = 1 and TIMEOUT = 0, then read DELTA.

## Verification

- `make test-puf_meas` (cocotb, `-DSIM`, RO periods random per `RO_SEED`)
  tests `ro_array` + `puf_meas` directly. For 24 random pairs in random
  order the counts must equal, exactly, the values predicted from the RO
  periods. The sign must match the faster RO, except inside the dead zone
  where Δ = 0 is required. |Δ| must be within 4 steps of the ideal. A
  monitor checks on every clk edge that only the selected pair is enabled
  and that no other RO output is high. It also checks ascending and
  descending pair sweeps, the `HALF_PERIOD_NS` parameter path and the
  timeout. The monitor and the pair-order test were checked against two
  mutated copies of `ro_array.v` (an extra pair enabled; A/B swapped); both
  were caught.
- `make test-ropuf` exercises the same core through the Avalon-MM
  registers: ID/PARAMS, exact counts for six pairs, repeatability, START
  while busy, and timeout.
- `make synth-check` (yosys generic synthesis): all 1024 NAND and 4096
  inverter stages survive. That check exists because the first version kept
  only `keep` attributes on the nets, and yosys still reduced every RO to a
  single inverter. Total ~10k generic cells. It also compiles the
  `CYCLONEV` path against a stand-in `lcell` (`tb/common/lcell_stub.v`).
  That only checks syntax and connectivity; the real primitive's effect can
  only be checked in Quartus.

## Before this runs on an FPGA

None of these steps can be done or checked from this repository yet:

- **Vendor synthesis.** Build with `CYCLONEV` defined for Quartus and
  confirm in the fitter report that every RO keeps 5 logic cells. For
  Vivado, use the generic path with `DONT_TOUCH` plus
  `ALLOW_COMBINATORIAL_LOOPS` on the stage nets.
- **Timing constraints.** Declare the RO outputs as clocks, or cut the paths
  between the RO domains and `clk`. Constrain the synchronizers (`ASYNC_REG`
  is set).
- **Placement.** Place the two ROs of a pair in identical, adjacent
  locations (LogicLock / Pblock per pair). Otherwise routing differences,
  not process variation, decide the bits.
- **RO frequency vs flip-flop toggle rate.** Only the first prescaler
  flip-flop runs at f_RO; the counter runs at f_RO/2^PRESCALE_LOG2. If timing
  still fails, raise `PRESCALE_LOG2` (coarser Δ) or `N_STAGES` (slower RO).
  The achievable RO frequency is unknown until measured.
- **Integration.** `fpga/char/quartus/` integrates the core on the
  DE10-Nano behind a JTAG to Avalon master, with a LogicLock region and an
  SDC for the points above; it has not been compiled yet. Steps:
  [`docs/char_howto.md`](../../docs/char_howto.md).
