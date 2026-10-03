# SIDIK

[![test](https://github.com/hudaubaya/SIDIK/actions/workflows/test.yml/badge.svg)](https://github.com/hudaubaya/SIDIK/actions/workflows/test.yml)

Hardware security building blocks for Tiny Tapeout / FPGA: a SHA-256 core
(Shaman), compared against RO-PUF and ECC baselines.

## Layout

| Path            | Contents |
|-----------------|----------|
| `rtl/`          | Synthesizable RTL: ring oscillators (`ro_cell.v`, `ro_array.v`), pair measurement (`puf_meas.v`), the Avalon-MM RO-PUF core built from them ([`rtl/ropuf/`](rtl/ropuf/README.md)), and the extended Hamming (72,64) SECDED syndrome decoder (`secded72.v`, bit-exact with `model/secded.py`). Third-party cores live unmodified in `rtl/third_party/<name>/` with their license and provenance. |
| `tb/`           | cocotb testbenches, one directory per DUT. |
| `model/`        | Python reference models: SHA-256 padding, and the RO-PUF key generator model ([`docs/puf-model.md`](docs/puf-model.md)). |
| `fpga/char/`    | FPGA characterization: DE10-Nano Quartus project with a JTAG to Avalon master, System Console measurement script, parameter fitting and reports ([`fpga/char/README.md`](fpga/char/README.md), team steps in [`docs/char_howto.md`](docs/char_howto.md)). Not yet run on a board. |
| `fpga/release/` | FPGA release builds. |
| `sw/`           | Host software: `analyze.py` computes the model's PUF metrics from measured race CSVs. |
| `docs/`         | Documentation; baseline sources and licenses are in [`docs/baselines.md`](docs/baselines.md). |

## Running the tests

Needs Python 3 (tested on 3.11), Icarus Verilog (tested on 12.0) and, for
the System Console script check, `tclsh` (`apt install tcl`; skipped if
missing). CI
(`.github/workflows/test.yml`) runs `make test` on every push to `main` and
every pull request.

```sh
sudo apt install iverilog
make install      # pip install -r requirements.txt
make test         # model, characterization, sw and RTL tests
make test-shaman  # one testbench (also: test-ropuf, test-puf_meas, test-secded72)
make test-mutants # the secded72 tests must fail on two mutated RTL copies
make synth-check  # yosys: check every RO keeps its stages (needs yosys)
make puf-model    # rerun the RO-PUF Monte Carlo (~2.5 min), rewrites docs/puf-model/
make clean
```

## Licensing

SIDIK is licensed under the GNU General Public License v3.0 or later
(`GPL-3.0-or-later`); see [`LICENSE`](LICENSE).

Third-party code keeps its own license, stated in its directory:
`rtl/third_party/shaman/` is GPL-3.0-or-later (Pat Deegan). See
[`docs/baselines.md`](docs/baselines.md#license-impact-on-sidik) for what this
means for SIDIK releases.
