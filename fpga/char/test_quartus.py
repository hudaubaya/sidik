# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Checks of the Quartus project and the System Console script without Quartus.

Quartus, Platform Designer and System Console are not available here, so
these tests check what can be checked offline:
- every name the QSF, SDC and Platform Designer scripts rely on (files,
  instances, registers, ports, parameters) exists in the RTL;
- the top level and the RTL file list of the component compile (iverilog,
  CYCLONEV path with a stand-in lcell and char_sys);
- measure_pairs.tcl follows the register protocol against a mock of the
  master service (tclsh), and its CSV loads in sw/analyze.py.
They do not show that Quartus accepts the assignments or that the design
works on the board.
"""

import importlib.util
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
Q = ROOT / "fpga" / "char" / "quartus"
SYSCON = ROOT / "fpga" / "char" / "syscon"
RTL = ROOT / "rtl"


def read(p):
    return Path(p).read_text()


def strip_comments(text, marker="#"):
    return "\n".join(line.split(marker, 1)[0] if line.lstrip().startswith(marker)
                     else line for line in text.splitlines())


def hw_tcl_files():
    text = read(Q / "sidik_ropuf_hw.tcl")
    return [RTL.joinpath(*m.split()) for m in
            re.findall(r"\[file join \$rtl ([^\]]+)\]", text)]


def module_ports(verilog, module):
    """{port: width} of `module` (ANSI header)."""
    m = re.search(rf"module\s+{module}\b.*?\((.*?)\);", verilog, re.S)
    ports = {}
    for d, rng, name in re.findall(
            r"(input|output)\s+(?:wire|reg)?\s*(?:signed\s*)?(\[[^\]]+\])?\s*(\w+)",
            m.group(1)):
        width = 1
        if rng:
            hi, lo = rng.strip("[]").split(":")
            width = int(hi) - int(lo) + 1 if hi.strip().isdigit() else None
        ports[name] = (d, width)
    return ports


class ProjectConsistencyTest(unittest.TestCase):
    def test_component_files_exist(self):
        files = hw_tcl_files()
        self.assertEqual(len(files), 5)
        for f in files:
            self.assertTrue(f.is_file(), f)

    def test_component_interface_matches_rtl(self):
        hw = strip_comments(read(Q / "sidik_ropuf_hw.tcl"))
        rtl = read(RTL / "ropuf" / "ropuf_avmm.v")
        ports = module_ports(rtl, "ropuf_avmm")
        declared = re.findall(r"add_interface_port\s+\w+\s+(\w+)\s+\w+\s+(Input|Output)\s+(\d+)",
                              hw)
        self.assertEqual({p for p, _, _ in declared}, set(ports))
        for port, direction, width in declared:
            d, w = ports[port]
            self.assertEqual(d, direction.lower(), port)
            self.assertEqual(w, int(width), port)
        for param in re.findall(r"add_parameter\s+(\w+)", hw):
            self.assertRegex(rtl, rf"parameter\s+(integer\s+)?{param}\b")
        self.assertIn("TOP_LEVEL ropuf_avmm", hw)

    def test_system_matches_component_and_top(self):
        sys_tcl = read(Q / "char_sys.tcl")
        name = re.search(r"set_module_property NAME (\w+)", read(Q / "sidik_ropuf_hw.tcl"))
        self.assertIn(f"add_instance ropuf_0 {name.group(1)}", sys_tcl)
        self.assertIn("baseAddress {0x0000}", sys_tcl)
        self.assertIn("base 0x0", read(SYSCON / "measure_pairs.tcl"))
        top = read(Q / "sidik_char_top.v")
        # Exported interfaces clk / reset -> ports clk_clk / reset_reset_n.
        self.assertIn("EXPORT_OF clk_0.clk_in\n", sys_tcl)
        self.assertIn(".clk_clk", top)
        self.assertIn(".reset_reset_n", top)

    def test_qsf_names_exist(self):
        qsf = strip_comments(read(Q / "sidik_char.qsf"))
        for f in re.findall(r"-name (?:VERILOG_FILE|SDC_FILE) (\S+)", qsf):
            self.assertTrue((Q / f).is_file(), f)
        top_name = re.search(r"TOP_LEVEL_ENTITY (\w+)", qsf).group(1)
        ports = module_ports(read(Q / "sidik_char_top.v"), top_name)
        pins = re.findall(r"set_location_assignment (PIN_\w+) -to (\w+)(?:\[\d+\])?", qsf)
        self.assertEqual(len({p for p, _ in pins}), len(pins), "duplicate pin")
        for _, port in pins:
            self.assertIn(port, ports)
        self.assertIn('VERILOG_MACRO "CYCLONEV=1"', qsf)
        core = read(RTL / "ropuf" / "ropuf_core.v")
        meas = read(RTL / "puf_meas.v")
        for mod, inst in re.findall(r"(\w+):(\w+)", " ".join(
                re.findall(r"LL_MEMBER_OF \w+ -to \"([^\"]+)\"", qsf))):
            if mod == "ropuf_core":
                self.assertRegex(read(RTL / "ropuf" / "ropuf_avmm.v"),
                                 rf"ropuf_core\s+#\(.*?\)\s+{inst}\b".replace(".*?", "[^;]*?"))
            else:
                self.assertRegex(core, rf"{mod}\s+#\([^;]*?\)\s+{inst}\b")
        for reg in ("halted_a_sync", "halted_b_sync", "stop_sync"):
            self.assertIn(reg, meas)

    def test_sdc_names_exist(self):
        sdc = strip_comments(read(Q / "sidik_char.sdc"))
        meas = read(RTL / "puf_meas.v")
        core = read(RTL / "ropuf" / "ropuf_core.v")
        for inst in ("u_cnt_a", "u_cnt_b"):
            self.assertRegex(meas, rf"puf_race_counter\s+#\([^;]*?\)\s+{inst}\b")
        self.assertIn("begin : g_div", meas)
        self.assertIn("reg [P-1:0] pre;", meas)
        self.assertRegex(core, r"puf_meas\s+#\([^;]*?\)\s+u_meas\b")
        self.assertRegex(core, r"ro_array\s+#\([^;]*?\)\s+u_array\b")
        # The SDC's divide-by-2 assumes one prescaler stage everywhere.
        self.assertIn("parameter integer PRESCALE_LOG2 = 1", meas)
        self.assertIn("parameter integer PRESCALE_LOG2 = 1", core)
        self.assertNotIn("PRESCALE_LOG2", read(RTL / "ropuf" / "ropuf_avmm.v"))
        self.assertIn("-divide_by 2", sdc)
        self.assertIn("set_false_path -through [get_nets -compatibility_mode {*u_core|u_array|*}]",
                      sdc)


@unittest.skipUnless(shutil.which("iverilog"), "iverilog not installed")
class TopCompileTest(unittest.TestCase):
    def test_top_compiles_with_component_files(self):
        srcs = [Q / "sidik_char_top.v", ROOT / "tb" / "common" / "char_sys_stub.v",
                ROOT / "tb" / "common" / "lcell_stub.v", *hw_tcl_files()]
        r = subprocess.run(["iverilog", "-g2012", "-DCYCLONEV", "-Wall", "-o", "/dev/null",
                            "-s", "sidik_char_top", *map(str, srcs)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


TCL_STUBS = """
foreach c {create_clock create_generated_clock set_input_delay set_output_delay
           set_false_path set_clock_groups derive_clock_uncertainty
           set_global_assignment set_instance_assignment set_location_assignment} {
    proc $c args { incr ::n_cmds }
}
foreach c {get_ports get_pins get_registers get_nets} {
    proc $c args { return [lindex $args end] }
}
# QSF names such as LED[0] or sync[*] are literal in Quartus; plain Tcl would
# run "0" / "*" as commands. Return them unchanged instead.
proc unknown args { return "\[[join $args]\]" }
set ::n_cmds 0
source [lindex $argv 0]
puts $::n_cmds
"""


@unittest.skipUnless(shutil.which("tclsh"), "tclsh not installed (apt install tcl)")
class TclSyntaxTest(unittest.TestCase):
    """QSF and SDC are Tcl: they must at least parse (brackets, quotes)."""

    def test_qsf_and_sdc_parse(self):
        for f in ("sidik_char.qsf", "sidik_char.sdc"):
            r = subprocess.run(["tclsh", "/dev/stdin", str(Q / f)], input=TCL_STUBS,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f + r.stderr)
            self.assertGreater(int(r.stdout.split()[-1]), 10, f)


@unittest.skipUnless(shutil.which("tclsh"), "tclsh not installed (apt install tcl)")
class SysconScriptTest(unittest.TestCase):
    def test_against_register_mock(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["tclsh", str(SYSCON / "test_measure_pairs.tcl"), d],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            spec = importlib.util.spec_from_file_location("sw_analyze",
                                                          ROOT / "sw" / "analyze.py")
            swa = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(swa)
            data = swa.load([Path(d) / "mock_pairs.csv"])
            self.assertEqual(data.source, "mock")
            self.assertEqual(data.delta.shape, (1, 1, 4, 6))
            # The mock's rep 1: |delta| = (13 pair + 7) % 40 + 2, pair 2 a tie,
            # RO b faster on odd pairs.
            self.assertEqual(data.delta[0, 0, 1].tolist(), [9, -22, 0, -8, 21, -34])
            with self.assertRaises(ValueError):
                swa.load([Path(d) / "mock_freq.csv"])


if __name__ == "__main__":
    unittest.main()
