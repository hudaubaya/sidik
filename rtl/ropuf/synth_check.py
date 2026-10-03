# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generic yosys synthesis of ropuf_avmm; fail if any RO lost stages.

    python3 rtl/ropuf/synth_check.py

Inverter pairs inside a ring oscillator are logically redundant, so a
synthesis tool is free to delete them unless they are preserved; this
checks that every RO still has its NAND and N_STAGES-1 inverters.
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
RTL = HERE.parent
SOURCES = [str(RTL / f) for f in ("ro_cell.v", "ro_array.v", "puf_meas.v",
                                  "ropuf/ropuf_core.v", "ropuf/ropuf_avmm.v")]
N_RO = 1024
N_STAGES = 5


def main():
    with tempfile.TemporaryDirectory() as tmp:
        stat = Path(tmp) / "stat.txt"
        script = (f"read_verilog {' '.join(SOURCES)}; "
                  f"synth -top ropuf_avmm -flatten; tee -q -o {stat} stat")
        # The RO loops are intentional; yosys warns about each of them.
        run = subprocess.run(["yosys", "-q", "-p", script], cwd=HERE,
                             capture_output=True, text=True)
        if run.returncode:
            sys.exit(run.stdout + run.stderr)
        text = stat.read_text()
    # No hierarchy section means no ro_stage cell survived at all.
    hier = text.split("=== design hierarchy ===", 1)[-1]
    counts = {int(m.group(1)): int(m.group(2)) for m in
              re.finditer(r"ro_stage\\NAND=s32'0*([01])\s+(\d+)", hier)}
    want = {1: N_RO, 0: N_RO * (N_STAGES - 1)}
    print(f"ro_stage NAND cells: {counts.get(1, 0)} (want {want[1]}), "
          f"inverter cells: {counts.get(0, 0)} (want {want[0]})")
    if counts != want:
        sys.exit("FAIL: RO stages were removed or merged by synthesis")
    total = re.search(r"Number of cells:\s+(\d+)", hier.split("Number of wires", 1)[-1])
    print(f"OK, {total.group(1) if total else '?'} generic cells in total")


if __name__ == "__main__":
    main()
