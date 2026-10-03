# SIDIK

Hardware security building blocks for Tiny Tapeout / FPGA: a SHA-256 core
(Shaman), compared against RO-PUF and ECC baselines.

## Layout

| Path            | Contents |
|-----------------|----------|
| `rtl/`          | Synthesizable RTL. Third-party cores live unmodified in `rtl/third_party/<name>/` with their license and provenance. |
| `tb/`           | cocotb testbenches, one directory per DUT. |
| `model/`        | Python reference models used by the testbenches. |
| `fpga/char/`    | FPGA characterization (measurement scripts, raw data, plots). |
| `fpga/release/` | FPGA release builds. |
| `sw/`           | Host / firmware software. |
| `docs/`         | Documentation; baseline sources and licenses are in [`docs/baselines.md`](docs/baselines.md). |

## Running the tests

Needs Python 3 (tested on 3.11) and Icarus Verilog (tested on 12.0).

```sh
sudo apt install iverilog
make install      # pip install -r requirements.txt
make test         # model unit tests + every RTL testbench
make test-shaman  # one testbench
make clean
```

## Licensing

`rtl/third_party/shaman/` is GPL-3.0-or-later (Pat Deegan). See
[`docs/baselines.md`](docs/baselines.md#license-impact-on-sidik) for what that
means for SIDIK releases. A project-level license has not been chosen yet.
