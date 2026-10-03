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
| `ro_cell.v` | One RO: NAND enable + (N_STAGES−1) inverters, each a preserved `ro_stage` instance. Behavioural oscillator under `SIM`. |
| `race_counter.v` | Counter clocked by one RO; stops at 2^LOG2N or when the other counter's `done` arrives through a 2-flop synchronizer. |
| `ropuf_core.v` | 1024 ROs, pair selection, the two counters, control FSM. |
| `ropuf_avmm.v` | Avalon-MM slave (register map below). |
| `synth_check.py` | yosys generic synthesis; fails if any RO lost a stage. |

Parameters: `N_RO` (1024, even), `N_STAGES` (5, odd), `LOG2N` (14),
`TIMEOUT_RESET` (2^20 clk cycles).

## How a measurement works

1. **CLEAR:** counters held in asynchronous reset, all ROs off.
2. **ARM:** reset released while the ROs are still off (no clock edges, so
   no recovery or removal hazard).
3. **RUN:** only ROs 2·pair and 2·pair+1 are enabled. The other 1022 stay
   off, which reduces power and coupling. Each counter is clocked by its own
   RO. The faster one stops at exactly 2^14. Its `done` reaches the slower
   counter through two flops in the slower RO's clock domain, and the slower
   counter then stops.
4. **STOP:** once both `halted` flags are seen in `clk` after 2-flop
   synchronizers, the ROs are switched off and the counts are captured.
   The counts no longer change once halted, so the multi-bit capture is
   safe.
5. If RUN exceeds `TIMEOUT` clk cycles, the measurement ends with TIMEOUT
   set. The captured counts are then diagnostic only.

`DELTA = COUNT_A − COUNT_B`: positive when RO 2·pair is faster.

**Measurement bias.** The synchronizer lets the slower counter run 2 more
of its own cycles after the winner reaches 2^14. With both ROs starting
together, |DELTA| is on average **1.5 counts smaller** than
2^14·|1 − f_slow/f_fast| (−2.0 to −1.0 over the 512 simulated pairs).
`fpga/char/analyze.py` corrects for this when the run's `meta.json` sets
`delta_magnitude_bias` to −1.5.

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

- `make test-ropuf` (cocotb, Icarus, `-DSIM`). It reads ID and PARAMS. For
  pairs 0, 1, 2, 100, 255 and 511 it checks that COUNT_A and COUNT_B equal,
  exactly, the values predicted from the behavioural RO periods, including
  the 2-cycle synchronizer overrun. It also checks repeatability, that START
  is ignored while busy, and the timeout path.
- `make synth-check` (yosys generic synthesis): all 1024 NAND and 4096
  inverter stages survive. That check exists because the first version kept
  only `keep` attributes on the nets, and yosys still reduced every RO to a
  single inverter. Total ~10k generic cells.

## Before this runs on an FPGA

None of these steps can be done or checked from this repository yet:

- **Vendor synthesis.** Confirm in the fitter report that every RO keeps 5
  LUTs: Quartus `synthesis keep`, and for Vivado `DONT_TOUCH` plus
  `ALLOW_COMBINATORIAL_LOOPS` on the stage nets.
- **Timing constraints.** Declare the RO outputs as clocks, or cut the paths
  between the RO domains and `clk`. Constrain the synchronizers (`ASYNC_REG`
  is set).
- **Placement.** Place the two ROs of a pair in identical, adjacent
  locations (LogicLock / Pblock per pair). Otherwise routing differences,
  not process variation, decide the bits.
- **RO frequency vs counter fmax.** The 15-bit counters must keep up with
  the RO. If a 5-stage RO is faster than the counter can run on the target
  device, increase `N_STAGES`. The achievable RO frequency is unknown until
  measured.
- **Integration.** Add the core to the Platform Designer system (e.g. HPS
  lightweight bridge on DE10-Nano) and implement `HardwareBackend` in
  `fpga/char/acquire.py`.
