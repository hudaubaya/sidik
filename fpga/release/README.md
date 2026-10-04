# FPGA release (DE10-Nano)

Two SIDIK key generators (`rtl/sidik_avmm.v`, release build: no read path to
K, key bits or raw counts) on one Cyclone V, each with its own PUF array in
its own LogicLock region:

| Instance | PUF array | LogicLock region | JTAG master | HPS (Linux) |
|---|---|---|---|---|
| `sidik_a` | larik A | `ro_region_a` | 0x000 | 0xFF240000 |
| `sidik_b` | larik B | `ro_region_b` | 0x100 | 0xFF240100 |

KEY0 is the tamper input of both instances: pressing it erases K and every
buffer of A and B within 3 clock cycles, and STATUS.TAMPERED stays set
until reset. Register map: `rtl/sidik_avmm.v`, `sw/sidik_regs.py`,
`sw/sidik_regs.h`.

> **Status:** nothing here has been compiled with Quartus or run on a board
> yet. What is checked (`make test-release`, `make test-sidik_system`): the
> scripts parse and do what is intended against the RTL hierarchy, the top
> level and the component's sources compile, and the two instances behind
> an address decoder model (`tb/sidik_system/sidik_window.v`) pass the
> verifier flows in simulation. The GHRD instance names in
> `add_to_ghrd.tcl` are assumptions (see below).

Releases containing the Shaman core are subject to GPL-3.0 (see
`docs/baselines.md`).

## Files

| File | Contents |
|---|---|
| `sidik_hw.tcl` | Platform Designer component `sidik`: Avalon-MM slave (64 words, read latency 1), clock, reset, conduit `tamper` (`tamper_n`). RTL referenced in place from `rtl/`. |
| `release_sys.tcl` | Stand-alone system `release_sys` (no HPS): clock, JTAG to Avalon master, `sidik_a`, `sidik_b`, tamper conduits exported. |
| `add_to_ghrd.tcl` | Adds `sidik_a`, `sidik_b` and a JTAG master `sidik_jtag` to the DE10-Nano GHRD `soc_system.qsys`, on the lightweight HPS-to-FPGA bridge at window 0x40000. |
| `sidik_assignments.tcl` | Quartus assignments for either project: `CYCLONEV`, LCELL buffers kept, no physical synthesis, synchronizers, LogicLock regions `ro_region_a` / `ro_region_b`, `sidik_ro.sdc`. |
| `sidik_ro.sdc` | RO clocks of both instances, ring oscillators cut from timing, tamper input. |
| `sidik_release_top.v`, `.qpf`, `.qsf`, `sidik_release_top.sdc` | Stand-alone project: KEY0 tamper, KEY1 reset, LED heartbeat / reset / KEY0. |
| `build.sh` | Builds the stand-alone project. |
| `syscon/sidik_bridge.tcl` | System Console bridge: serves the JTAG master's view of the window on 127.0.0.1 for `verifier.py --jtag` / `sidik_verifier -j`. |
| `test_release.py` | Offline checks (`make test-release`). |

## Stand-alone build (JTAG only)

```sh
cd fpga/release
./build.sh program              # Quartus Prime with Cyclone V on PATH
# expect "combinational-loop warnings: 2048" (two arrays of 1024 rings)
system-console -cli --script=syscon/sidik_bridge.tcl &     # 127.0.0.1:2540
python3 sw/verifier.py --jtag 127.0.0.1:2540 clone-demo
```

After the first good compile, lock both LogicLock regions
(`sidik_assignments.tcl` explains how) so later bitstreams keep A and B in
the same places, and check that the regions do not overlap.

## HPS build (lightweight bridge + JTAG)

1. Copy the GHRD from the DE10-Nano CD
   (`Demonstrations/SoC_FPGA/DE10_NANO_SoC_GHRD`).
2. Add the instances to its system:
   ```sh
   qsys-script --system-file=soc_system.qsys \
       --script=<repo>/fpga/release/add_to_ghrd.tcl \
       --search-path="<repo>/fpga/release,$"
   ```
   It assumes the GHRD names `hps_0.h2f_lw_axi_master`, `clk_0.clk`,
   `clk_0.clk_reset` and `hps_0.h2f_reset`. If Platform Designer reports a
   missing instance, override them, e.g.
   `--cmd="set lw_master <name>.h2f_lw_axi_master"`. Check in the address
   map that 0x40000-0x401FF on the lightweight bridge is free; another
   window is `--cmd="set window 0x..."` here and `--window` / `-w` in the
   verifiers.
3. In the GHRD top level (`DE10_NANO_SoC_GHRD.v`), connect the new
   `soc_system` ports to KEY0:
   ```verilog
   .sidik_a_tamper_tamper_n (KEY[0]),
   .sidik_b_tamper_tamper_n (KEY[0]),
   ```
4. Apply the SIDIK assignments to the GHRD project, then generate and compile:
   ```sh
   quartus_sh -t <repo>/fpga/release/sidik_assignments.tcl DE10_NANO_SoC_GHRD
   ```
5. On the board (Linux on the HPS, as root), build and run the verifier:
   ```sh
   gcc -O2 -Wall -o sidik_verifier sw/sidik_verifier.c
   ./sidik_verifier -m enroll -i A -d a.db -n 16
   ./sidik_verifier -m auth -i A -d a.db         # ACCEPT, one challenge used
   ./sidik_verifier -m auth -i B -d a.db         # clone: REJECT
   ./sidik_verifier -m clone-demo
   ```
   (`python3 sw/verifier.py --mem ...` does the same.) Press KEY0 during or
   after a session: the next one reports `device tampered` until reset.

## Simulation

`tb/sidik_system` simulates both instances behind `sidik_window.v`, a model
of the interconnect's address decoding (A at 0x000, B at 0x100, word =
address[7:2], nothing elsewhere), with KEY0 shared. In simulation instance
B uses a different set of behavioural ring oscillators (`RO_INDEX_BASE`),
so A and B behave as two chips. `sw/verifier.py` runs unchanged on it, the
C verifier through a socket.
