# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mutation check for the sidik_avmm testbench.

    python3 tb/sidik_avmm/mutants.py

Each mutant is a one-place edit of rtl/sidik_avmm.v and must make the test
aimed at it fail (release build). The unmutated RTL is covered by
`make test-sidik_avmm`; to keep the time down only the targeted test runs
per mutant (with --control, each targeted test also runs on the original).
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TB = Path(__file__).resolve().parent
RTL = TB.parents[1] / "rtl" / "sidik_avmm.v"

# name -> (original text, replacement, test that must fail)
MUTANTS = {
    # sidik_crypto not cleared by tamper / CLEAR: K survives.
    "crypto_not_zeroized": (
        ".clk(clk), .rst(rst), .zeroize(zeroize), .start(cr_start), .op(cr_op),",
        ".clk(clk), .rst(rst), .zeroize(1'b0), .start(cr_start), .op(cr_op),",
        "test_tamper_erases_within_three_cycles"),
    # FSM default branch returns to idle without CLEAR.
    "illegal_state_no_clear": (
        "                    illegal <= 1'b1;\n",
        "",
        "test_illegal_state_clears"),
    # A raw counter reaches the read mux in the release build.
    "raw_counter_readable": (
        "            if (avs_address == A_KCV)    avs_readdata <= kcv_q;\n",
        "            if (avs_address == A_KCV)    avs_readdata <= kcv_q;\n"
        "            if (avs_address == 6'd63)    avs_readdata <= core_count_a;\n",
        "test_flow_and_address_scan"),
}


def run(rtl: Path, work: Path, testcase: str):
    """Run one test on `rtl`; return True if it passed."""
    results = work / "results.xml"
    env = dict(os.environ, COCOTB_RESULTS_FILE=str(results))
    subprocess.run(
        ["make", "-C", str(TB), f"AVMM_RTL={rtl}", f"SIM_BUILD={work / 'sim_build'}",
         f"TESTCASE={testcase}"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not results.exists():
        raise RuntimeError(f"no results for {rtl} (did it compile?)")
    cases = list(ET.parse(results).getroot().iter("testcase"))
    if [c.get("name") for c in cases] != [testcase]:
        raise RuntimeError(f"{testcase} did not run for {rtl}")
    return cases[0].find("failure") is None


def main():
    control = "--control" in sys.argv
    source = RTL.read_text()
    for name, (original, _, _) in MUTANTS.items():
        if source.count(original) != 1:
            sys.exit(f"mutant {name}: anchor not found exactly once in {RTL}")
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, (original, replacement, testcase) in MUTANTS.items():
            if control:
                work = tmp / f"{name}_original"
                work.mkdir()
                if not run(RTL, work, testcase):
                    sys.exit(f"FAIL: {testcase} fails on the unmutated RTL")
            work = tmp / name
            work.mkdir()
            mutant = work / "sidik_avmm.v"
            mutant.write_text(source.replace(original, replacement))
            passed = run(mutant, work, testcase)
            print(f"mutant {name}: {testcase} {'PASSES (survived)' if passed else 'fails'}")
            if passed:
                survived.append(name)
    if survived:
        sys.exit(f"FAIL: mutants survived: {', '.join(survived)}")
    print("OK: every mutant is killed")


if __name__ == "__main__":
    main()
