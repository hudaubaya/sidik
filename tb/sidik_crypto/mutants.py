# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mutation check for the sidik_crypto testbench.

    python3 tb/sidik_crypto/mutants.py

Runs the cocotb tests on rtl/sidik_crypto.v (must pass) and on mutated
copies (each must make at least one test fail): the Shaman protocol rules
(gap after the 64th byte, capture one cycle after resultNext) and the
erasure of the core and of the inner digest. CRYPTO_CASES random cases per
run (default 10).
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TB = Path(__file__).resolve().parent
RTL = TB.parents[1] / "rtl" / "sidik_crypto.v"
CASES = os.environ.get("CRYPTO_CASES", "10")

# name -> (original text, replacement); each original must occur once.
MUTANTS = {
    # next block strobed right after the 64th byte, without waiting for the
    # block to be processed (busy rises one cycle late). Note: dropping only
    # the GAP state is an equivalent mutant: S_WAIT is first evaluated after
    # the strobe's low cycle, when busy is already high.
    "strobe_right_after_64th_byte": (
        "                        wait_n <= GAP - 1;\n"
        "                        state  <= S_GAP;\n",
        "                        blk    <= 1'b1;\n"
        "                        idx    <= 6'd0;\n"
        "                        state  <= last_blk ? S_GAP : S_HI;\n"),
    # capture resultbyteOut in the cycle right after resultNext drops
    "capture_too_early": (
        "                    wait_n  <= 4'd1;\n",
        "                    wait_n  <= 4'd0;\n"),
    # core not reset at the end of each hash
    "no_core_reset_after_hash": (
        "                            core_rst <= 1'b1;     // ERASE: core state\n",
        ""),
    # inner HMAC digest left in its register
    "no_inner_digest_erase": (
        "                            inner_q <= 256'd0;    // ERASE: inner digest\n",
        ""),
}


def run(rtl: Path, work: Path):
    """Run the testbench on `rtl`; return {test name: passed}."""
    results = work / "results.xml"
    env = dict(os.environ, COCOTB_RESULTS_FILE=str(results), CRYPTO_CASES=CASES)
    subprocess.run(
        ["make", "-C", str(TB), f"CRYPTO_RTL={rtl}", f"SIM_BUILD={work / 'sim_build'}"],
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
    for name, (original, _) in MUTANTS.items():
        if source.count(original) != 1:
            sys.exit(f"mutant {name}: anchor not found exactly once in {RTL}")
    survived = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "original").mkdir()
        control = run(RTL, tmp / "original")
        print(f"original: {sum(control.values())}/{len(control)} tests pass ({CASES} cases)")
        if not all(control.values()):
            sys.exit("FAIL: the unmutated RTL does not pass")
        for name, (original, replacement) in MUTANTS.items():
            work = tmp / name
            work.mkdir()
            mutant = work / "sidik_crypto.v"
            mutant.write_text(source.replace(original, replacement))
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
