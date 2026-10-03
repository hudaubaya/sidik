# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mutation check for the fuzzy_ext testbench.

    python3 tb/fuzzy_ext/mutants.py

Runs the cocotb tests on rtl/fuzzy_ext.v (must pass) and on mutated copies
(each must make at least one test fail). Mainly the buffer erasure: every
place that clears response bits, the key buffer or the vote counter, and
the gating of the key output. Three functional mutants check that the
model comparison itself bites. The mutant runs use FE_CHIPS virtual chips
(default 20) to keep the time down; the control run uses the same number.
"""

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TB = Path(__file__).resolve().parent
RTL = TB.parents[1] / "rtl" / "fuzzy_ext.v"
CHIPS = os.environ.get("FE_CHIPS", "20")

# name -> (original text, replacement); each original must occur once.
MUTANTS = {
    # --- buffer erasure ---
    "no_key_erase": (
        "                    key_q  <= {N_KEY{1'b0}};       // ZEROIZE: key buffer\n",
        ""),
    "no_response_erase_per_pair": (
        "                            sign_q[p] <= 1'b0;\n"
        "                            pass_q[p] <= 1'b0;\n",
        ""),
    "no_response_erase_final": (
        "                    sign_q <= {N_PAIRS{1'b0}};     // ZEROIZE: response bits\n"
        "                    pass_q <= {N_PAIRS{1'b0}};\n",
        ""),
    "no_vote_erase": (
        "                        votes      <= {CW{1'b0}};   // ZEROIZE: vote count consumed\n",
        ""),
    "key_port_ungated": (
        "assign key       = key_valid ? key_q : {N_KEY{1'b0}};",
        "assign key       = key_q;"),
    # --- function (the model comparison must catch these) ---
    "majority_off_by_one": (
        "key_q[sel] <= votes > N_VOTES / 2;",
        "key_q[sel] <= votes >= N_VOTES / 2;"),
    "kcv_mismatch_redoes_nothing": (
        "                                redo     <= {NB{1'b1}};\n",
        "                                redo     <= det;\n"),
    "one_remeasure_less": (
        "                    end else if (round != MAX_REMEASURE) begin\n",
        "                    end else if (round != MAX_REMEASURE - 1) begin\n"),
}


def run(rtl: Path, work: Path):
    """Run the testbench on `rtl`; return {test name: passed}."""
    results = work / "results.xml"
    env = dict(os.environ, COCOTB_RESULTS_FILE=str(results), FE_CHIPS=CHIPS)
    subprocess.run(
        ["make", "-C", str(TB), f"FE_RTL={rtl}", f"SIM_BUILD={work / 'sim_build'}"],
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
        print(f"original: {sum(control.values())}/{len(control)} tests pass ({CHIPS} chips)")
        if not all(control.values()):
            sys.exit("FAIL: the unmutated RTL does not pass")
        for name, (original, replacement) in MUTANTS.items():
            work = tmp / name
            work.mkdir()
            mutant = work / "fuzzy_ext.v"
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
