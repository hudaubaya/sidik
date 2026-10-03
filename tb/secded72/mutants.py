# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mutation check for the secded72 testbench.

    python3 tb/secded72/mutants.py

Runs the cocotb tests on the original RTL (must pass) and on mutated copies
of rtl/secded72.v (each must make at least one test fail). The RTL itself
has no mutation hooks; each mutant is a one-place textual edit.
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TB = Path(__file__).resolve().parent
RTL = TB.parents[1] / "rtl" / "secded72.v"

ORIGINAL = "h_col = {1'b1, j[6:0]};"
MUTANTS = {
    # One bit of the parity-check matrix flipped: column 37 (8'hA5) becomes
    # 8'hA1, the same as column 33.
    "flip_one_H_bit": "h_col = {1'b1, j[6:0]} ^ ((j == 37) ? 8'h04 : 8'h00);",
    # Overall-parity row disabled: syndrome bit 7 is always 0.
    "no_overall_parity": "h_col = {1'b0, j[6:0]};",
}


def run(rtl: Path, work: Path):
    """Run the testbench on `rtl`; return {test name: passed}."""
    results = work / "results.xml"
    env = dict(os.environ, COCOTB_RESULTS_FILE=str(results))
    subprocess.run(
        ["make", "-C", str(TB), f"SECDED_RTL={rtl}", f"SIM_BUILD={work / 'sim_build'}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not results.exists():
        raise RuntimeError(f"no results for {rtl} (did it compile?)")
    outcome = {}
    for case in ET.parse(results).getroot().iter("testcase"):
        outcome[case.get("name")] = case.find("failure") is None
    if not outcome:
        raise RuntimeError(f"no tests ran for {rtl}")
    return outcome


def main():
    source = RTL.read_text()
    if source.count(ORIGINAL) != 1:
        sys.exit(f"mutation anchor not found exactly once in {RTL}")
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        control = run(RTL, tmp / "original")
        print(f"original: {sum(control.values())}/{len(control)} tests pass")
        if not all(control.values()):
            sys.exit("FAIL: the unmutated RTL does not pass")
        for name, replacement in MUTANTS.items():
            work = tmp / name
            work.mkdir()
            mutant = work / "secded72.v"
            mutant.write_text(source.replace(ORIGINAL, replacement))
            outcome = run(mutant, work)
            failed = sorted(t for t, ok in outcome.items() if not ok)
            print(f"mutant {name}: {len(failed)}/{len(outcome)} tests fail "
                  f"({', '.join(failed) or 'none'})")
            if not failed:
                survived.append(name)
    if survived:
        sys.exit(f"FAIL: mutants survived: {', '.join(survived)}")
    print("OK: every mutant is killed")


if __name__ == "__main__":
    main()
