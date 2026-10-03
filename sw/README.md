# Software

Host and firmware software that drives the SIDIK cores.

| File | Contents |
|---|---|
| `analyze.py` | PUF metrics from raw RO-PUF race CSVs (`fpga/char/syscon/measure_pairs.tcl`), with the definitions of the model (`model/puf_montecarlo.py`): uniformity, uniqueness, reliability vs temperature, pairs passing the mask, key BER, reconstruction failures (recorded races replayed through enrollment, majority vote, SECDED and KCV). `--export-run` converts to the `fpga/char/` run format. |
| `test_analyze.py` | `make test-sw`: same decisions as `ro_puf.reconstruct()` on identical votes, hand-made error patterns, loader checks, metrics on model-simulated CSVs. |

Usage: [`docs/char_howto.md`](../docs/char_howto.md), step 7.
