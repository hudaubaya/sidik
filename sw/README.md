# Software

Host and firmware software that drives the SIDIK cores.

| File | Contents |
|---|---|
| `analyze.py` | PUF metrics from raw RO-PUF race CSVs (`fpga/char/syscon/measure_pairs.tcl`), with the definitions of the model (`model/puf_montecarlo.py`): uniformity, uniqueness, reliability vs temperature, pairs passing the mask, key BER, reconstruction failures (recorded races replayed through enrollment, majority vote, SECDED and KCV). `--export-run` converts to the `fpga/char/` run format. |
| `test_analyze.py` | `make test-sw`: same decisions as `ro_puf.reconstruct()` on identical votes, hand-made error patterns, loader checks, metrics on model-simulated CSVs. |
| `verifier.py` | SIDIK verifier: `enroll` (helper data, ID and N challenge-response pairs into a database), `auth` (helper data back, RECONSTRUCT, ID check, one unused challenge, constant-time compare), `clone-demo` (A's helper data applied to B). Transports: `--mem` (`/dev/mem`, lightweight HPS bridge), `--jtag` (`fpga/release/syscon/sidik_bridge.tcl`), `--sim-socket` / `--sim` (model). |
| `sidik_verifier.c` | The same verifier in C for Linux on the HPS (`-m`, `-j`, `-s`), same database file and exit codes. `gcc -O2 -Wall -o sidik_verifier sidik_verifier.c` |
| `sidik_regs.py`, `sidik_regs.h` | Register map of `rtl/sidik_avmm.v` as byte offsets, and the instance offsets of `fpga/release`. |
| `sidik_sim.py` | Model of the two instances behind the release register map (`model/ro_puf.py`), in process or served on a UNIX socket. |
| `test_verifier.py` | `make test-sw`: verifier flows on the model, C and Python sharing one database, register constants against the RTL and `fpga/release`. |

Usage: [`docs/char_howto.md`](../docs/char_howto.md) step 7 (`analyze.py`),
[`fpga/release/README.md`](../fpga/release/README.md) (verifiers).

## Verifier

Enrollment runs once per device, in a trusted setting. The device computes
K, KCV, ID and the responses; the verifier never sees K. It stores:

```
# sidik-verifier db v1
id <64 hex>
helper <18 words: pair mask (16), syndromes, KCV>
crp <challenge 64 hex> <response 64 hex> <0 unused | 1 used>
```

Each authentication uses one unused challenge. The challenge is marked used
and the database saved (atomically) *before* it is sent to the device, so a
challenge is never sent twice, even after a crash or a failed session.
When none is left the verifier refuses: enroll again. Every session ends
with CLEAR, so K does not stay in the device between sessions.

Exit codes: 0 accepted (or command done), 1 rejected, 2 error.

Limits: the database holds responses, so it must be kept as secret as the
devices' keys, and its integrity matters (an attacker who can reset the
`used` flags can replay recorded responses). The number of authentications
per enrollment is the number of stored pairs.

```sh
python3 sw/sidik_sim.py --socket /tmp/sidik.sock &         # model of A and B
python3 sw/verifier.py --sim-socket /tmp/sidik.sock enroll --instance A --db a.db -n 4
python3 sw/verifier.py --sim-socket /tmp/sidik.sock auth --instance A --db a.db   # ACCEPT
python3 sw/verifier.py --sim-socket /tmp/sidik.sock auth --instance B --db a.db   # REJECT (clone)
python3 sw/verifier.py --sim clone-demo
```
