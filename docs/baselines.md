# Baselines

External designs SIDIK is compared against or builds on. Each entry pins the
exact upstream commit that was reviewed, so results stay reproducible even if
upstream changes. None of the baseline sources below are vendored into this
repo yet; only the Shaman core is (see the last section).

Reviewed on 2026-10-03.

## RO-PUF: `litneet64/tt07-RO-based-PUF`

| Item        | Value |
|-------------|-------|
| Upstream    | https://github.com/litneet64/tt07-RO-based-PUF |
| Commit      | `1f840fe5ad533e60e30bfa1e274fcc20adfdc6c9` (2024-06-12) |
| Author      | Pablo Aravena |
| License     | **Apache-2.0** (repo `LICENSE`; `src/tt_um_ro_puf.v` also carries `SPDX-License-Identifier: Apache-2.0`) |
| Title       | RO-based Physically Unclonable Function (PUF) |
| Language    | Verilog |
| Top module  | `tt_um_litneet64_ro_puf` (file `src/tt_um_ro_puf.v`) |
| Sources     | `arbiter.v`, `counter.v`, `mux_16.v`, `ring_osc.v`, `puf_bit.v`, `tt_um_ro_puf.v` |
| TT tiles    | 1x2, clock 10 MHz |
| Interface   | `ui_in[7:0]` = 8-bit challenge, `uo_out[7:0]` = 8-bit response, `uio` unused |

Architecture (from upstream `docs/info.md`): 8 identical blocks, one response
bit each. Each block has 32 seven-inverter ring oscillators; two 16:1 muxes
pick one RO from each half based on the challenge, each feeds a counter, and
an arbiter outputs whichever counter reaches 65535 first.

Caveats for reuse:

- `ring_osc.v` instantiates the Sky130 standard cell `sky130_fd_sc_hd__inv_2`
  directly. It does not build for FPGA as-is; an FPGA port needs a
  replacement inverter primitive plus attributes that stop synthesis from
  removing or optimizing the combinational loop.
- The upstream cocotb test (`test/test.py`) is the Tiny Tapeout template
  with every statement commented out. It always passes and checks nothing.
- RTL simulation cannot model the process variation a PUF depends on, so
  uniqueness, reliability, uniformity and entropy can only come from silicon
  or FPGA measurements (`fpga/char/`).
- Test deps upstream: `cocotb==1.8.1`, `pytest==8.1.1`.

Apache-2.0 obligations if code is copied in: keep the copyright and license
notice, include the Apache-2.0 text, and mark modified files as changed.
Apache-2.0 code can be combined into a GPL-3.0 work (it is one-way
compatible: the combined work is distributed under GPL-3.0).

## ECC_test1: `vaibhav-neema/ECC`

| Item        | Value |
|-------------|-------|
| Upstream    | https://github.com/vaibhav-neema/ECC |
| Commit      | `7e0688af8c999e141ee9c68a488dc6dac3680767` (2024-05-29) |
| Author      | Dr. Vaibhav Neema |
| License     | **Apache-2.0** (repo `LICENSE`; no per-file headers) |
| Title       | ECC_test1 |
| Language    | Wokwi (schematic) |
| Wokwi ID    | `399192124046955521` (https://wokwi.com/projects/399192124046955521) |
| TT tiles    | 1x1, no clock (`clock_hz: 0`) |
| Interface   | `ui_in[7:0]` data in, `uo_out[7:0]` data out, `uio[3:0]` BIST error-inject inputs, `uio[7:4]` error position / redundant bits |

Function (from upstream `docs/info.md`): single-bit error detection and
correction. The transmitter adds 4 redundant bits to 8 data bits (a 12-bit
Hamming-style codeword), a BIST block can inject a 1-bit error, and the
receiver corrects it and reports the error position.

Caveats for reuse:

- **There is no HDL in the repository.** `src/` only has `cells.v` (the
  Wokwi cell-to-Verilog mapping) and `config.tcl`. The netlist is pulled from
  the Wokwi project at build time by the Tiny Tapeout GitHub Action. Using
  this as a baseline means exporting the netlist from Wokwi (or the TT build
  artifacts) and pinning that export, because the Wokwi project can change
  without a commit here.
- No functional test: `test/` contains only `requirements.txt`
  (`cocotb==1.8.1`, `pytest==8.1.1`).
- The license of the Wokwi project itself is not stated separately; the
  repo-level Apache-2.0 is the only grant found.

## Vendored: Shaman SHA-256 core

| Item        | Value |
|-------------|-------|
| Upstream    | https://github.com/psychogenic/tt05-shaman |
| Commit      | `05ea9e32a095a70ae8e52b05a7799e1dbca29ad3` (2023-10-29) |
| Author      | Pat Deegan |
| License     | **GPL-3.0-or-later** (file header). The upstream repo-level `LICENSE` is Apache-2.0 from the TT template; the file's own grant is the one that applies. |
| Location    | `rtl/third_party/shaman/` (unmodified, with `LICENSE` and provenance `README.md`) |

## License impact on SIDIK

Because the Shaman core is GPL-3.0-or-later, any SIDIK release that ships a
bitstream, netlist or GDS containing it must be distributable under GPL-3.0
(source available, same license). Apache-2.0 baselines are compatible with
that. A project-level `LICENSE` for SIDIK has not been chosen yet; it should
be GPL-3.0-or-later or a license that lets the combined work be released
under GPL-3.0.
