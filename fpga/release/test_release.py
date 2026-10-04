# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Offline checks of fpga/release (no Quartus here).

- The component's files, ports and parameters match rtl/sidik_avmm.v; no
  CHAR_BUILD in the release.
- The Platform Designer scripts, the Quartus assignment script, the QSF and
  both SDC files are run in tclsh with recording stubs: every command
  parses, and what they would do is checked (instance offsets, the masters
  of each instance, LogicLock members and synchronizer targets against the
  RTL hierarchy, RO clocks per instance).
- The top level and the component's RTL compile (iverilog, CYCLONEV path,
  stand-ins for lcell and release_sys).
- syscon/sidik_bridge.tcl serves the verifier protocol against a mock of
  the System Console master service, and picks the master by MAGIC.
None of this shows that Quartus accepts the project or that the design
works on the board.
"""

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REL = ROOT / "fpga" / "release"
RTL = ROOT / "rtl"
sys.path.insert(0, str(ROOT / "sw"))
import sidik_regs as R  # noqa: E402
import verifier as V  # noqa: E402


def read(p):
    return Path(p).read_text(encoding="utf-8")


def hw_tcl_files():
    return [RTL.joinpath(*m.split()) for m in
            re.findall(r"\[file join \$rtl ([^\]]+)\]", read(REL / "sidik_hw.tcl"))]


def module_ports(verilog, module):
    """{port: (direction, width)} of `module` (ANSI header, numeric ranges)."""
    m = re.search(rf"module\s+{module}\b\s*(?:#\s*\(.*?\)\s*)?\((.*?)\);", verilog, re.S)
    ports = {}
    for d, rng, name in re.findall(
            r"(input|output)\s+(?:wire|reg)?\s*(?:signed\s*)?(\[[^\]]+\])?\s*(\w+)", m[1]):
        width = 1
        if rng:
            hi, lo = rng.strip("[]").split(":")
            width = int(hi) - int(lo) + 1
        ports[name] = (d, width)
    return ports


# Tcl prelude: every Quartus / Platform Designer / TimeQuest command records
# itself as one tab-separated line "name<TAB>arg<TAB>arg..." on stdout.
TCL_RECORDER = r"""
rename package _package
proc package {args} {
    if {[lindex $args 0] eq "require"} { return 1 }
    return [_package {*}$args]
}
foreach c {create_system set_project_property add_instance set_instance_parameter_value
           add_connection set_connection_parameter_value add_interface
           set_interface_property save_system load_system
           set_module_property add_fileset set_fileset_property add_fileset_file
           add_parameter set_parameter_property add_interface_port
           project_open project_close export_assignments
           set_global_assignment set_instance_assignment set_location_assignment
           create_clock create_generated_clock set_input_delay set_output_delay
           set_false_path set_clock_groups derive_clock_uncertainty} {
    proc $c args "puts \[join \[list $c {*}\$args\] \\t\]"
}
foreach c {get_ports get_pins get_registers get_nets get_clocks} {
    proc $c args { return [lindex $args end] }
}
# QSF lines such as KEY[0] are literal in Quartus; plain Tcl would run "0".
proc unknown args { return "\[[join $args]\]" }
array set quartus {args {sidik_release}}
set argv [lrange $argv 1 end]
source [lindex $::script_argv 0]
"""


def run_tcl(path, *pre):
    """Source `path` under the recorder; returns [(cmd, [args])]."""
    prelude = "set ::script_argv [list {%s}]\n" % path + "\n".join(pre) + TCL_RECORDER
    r = subprocess.run(["tclsh"], input=prelude, capture_output=True, text=True,
                       cwd=Path(path).parent)
    if r.returncode:
        raise AssertionError(f"{path}: {r.stderr}")
    return [(ln.split("\t")[0], ln.split("\t")[1:]) for ln in r.stdout.splitlines() if ln]


def cmds(rec, name):
    return [a for c, a in rec if c == name]


class ComponentTest(unittest.TestCase):
    def test_files_exist_and_are_the_avmm_sources(self):
        files = hw_tcl_files()
        self.assertEqual(len(files), 9)
        for f in files:
            self.assertTrue(f.is_file(), f)
        mk = read(ROOT / "Makefile")
        avmm = re.search(r"AVMM_SOURCES := (.*?)\n\n", mk, re.S)[1].replace("\\\n", " ").split()
        self.assertEqual({ROOT / f for f in avmm}, set(files))

    def test_interface_matches_rtl(self):
        hw = read(REL / "sidik_hw.tcl")
        rtl = read(RTL / "sidik_avmm.v")
        ports = module_ports(rtl, "sidik_avmm")
        declared = re.findall(r"add_interface_port\s+\w+\s+(\w+)\s+\w+\s+(Input|Output)\s+(\d+)",
                              hw)
        self.assertEqual({p for p, _, _ in declared}, set(ports))
        for port, direction, width in declared:
            self.assertEqual(ports[port], (direction.lower(), int(width)), port)
        for param in re.findall(r"add_parameter\s+(\w+)", hw):
            self.assertRegex(rtl, rf"parameter\s+(integer\s+)?{param}\b")
        self.assertIn("TOP_LEVEL sidik_avmm", hw)
        self.assertEqual(2 ** ports["avs_address"][1] * 4, R.WINDOW_BYTES)

    def test_no_char_build_in_release(self):
        for f in REL.rglob("*"):
            if f.is_file() and f.suffix in (".tcl", ".qsf", ".sdc", ".v", ".sh"):
                code = "\n".join(ln for ln in read(f).splitlines()
                                 if not ln.lstrip().startswith(("#", "//")))
                self.assertNotIn("CHAR_BUILD", code, f)


@unittest.skipUnless(shutil.which("tclsh"), "tclsh not installed (apt install tcl)")
class ScriptsTest(unittest.TestCase):
    def test_component_script(self):
        rec = run_tcl(REL / "sidik_hw.tcl")
        name = cmds(rec, "set_module_property")[0]
        self.assertEqual(name, ["NAME", "sidik"])
        self.assertIn(["tamper", "tamper_n", "tamper_n", "Input", "1"],
                      cmds(rec, "add_interface_port"))
        for _, _, _, path, *_ in cmds(rec, "add_fileset_file"):
            self.assertTrue((REL / path).resolve().is_file(), path)

    def offsets(self, rec):
        """{(master, instance): base} from add_connection + baseAddress."""
        out = {}
        for conn, param, value in cmds(rec, "set_connection_parameter_value"):
            if param == "baseAddress":
                master, slave = conn.split("/")
                out[(master, slave.split(".")[0])] = int(value, 16)
        return out

    def test_release_system(self):
        rec = run_tcl(REL / "release_sys.tcl")
        inst = {a[0]: a[1] for a in cmds(rec, "add_instance")}
        self.assertEqual(inst["sidik_a"], "sidik")
        self.assertEqual(inst["sidik_b"], "sidik")
        self.assertEqual(self.offsets(rec), {("jtag_master.master", "sidik_a"): R.INSTANCE_OFFSET["A"],
                                             ("jtag_master.master", "sidik_b"): R.INSTANCE_OFFSET["B"]})
        exported = {a[0]: a[2] for a in cmds(rec, "set_interface_property") if a[1] == "EXPORT_OF"}
        self.assertEqual(exported["sidik_a_tamper"], "sidik_a.tamper")
        self.assertEqual(exported["sidik_b_tamper"], "sidik_b.tamper")
        top = read(REL / "sidik_release_top.v")
        self.assertIn(".sidik_a_tamper_tamper_n (KEY[0])", top)
        self.assertIn(".sidik_b_tamper_tamper_n (KEY[0])", top)
        self.assertIn("negedge KEY[1]", top)

    def test_ghrd_script(self):
        rec = run_tcl(REL / "add_to_ghrd.tcl")
        lw = "hps_0.h2f_lw_axi_master"
        a, b = R.INSTANCE_OFFSET["A"], R.INSTANCE_OFFSET["B"]
        self.assertEqual(self.offsets(rec), {
            (lw, "sidik_a"): V.DEFAULT_WINDOW + a, (lw, "sidik_b"): V.DEFAULT_WINDOW + b,
            ("sidik_jtag.master", "sidik_a"): a, ("sidik_jtag.master", "sidik_b"): b})
        self.assertIn(["save_system"], [[c, *a] for c, a in rec if c == "save_system"])
        # overridable GHRD names and window
        rec = run_tcl(REL / "add_to_ghrd.tcl", "set lw_master h.m", "set window 0x80000")
        self.assertEqual(self.offsets(rec)[("h.m", "sidik_b")], 0x80000 + b)
        c_src = read(ROOT / "sw" / "sidik_verifier.c")
        self.assertIn(f"#define DEFAULT_WINDOW  0x{V.DEFAULT_WINDOW:x}ul", c_src)
        self.assertIn(f"#define LW_BRIDGE_BASE  0x{V.LW_BRIDGE_BASE:X}ul", c_src)

    def test_assignments_match_rtl_hierarchy(self):
        rec = run_tcl(REL / "sidik_assignments.tcl")
        members = {}
        for a in cmds(rec, "set_instance_assignment"):
            if a[:2] == ["-name", "LL_MEMBER_OF"]:
                members.setdefault(a[2], []).append(a[4])
        self.assertEqual(members, {
            f"ro_region_{x}": [f"*sidik_avmm:sidik_{x}|ropuf_core:u_core|ro_array:u_array",
                               f"*sidik_avmm:sidik_{x}|ropuf_core:u_core|puf_meas:u_meas"]
            for x in "ab"})
        avmm, core = read(RTL / "sidik_avmm.v"), read(RTL / "ropuf" / "ropuf_core.v")
        self.assertRegex(avmm, r"ropuf_core\s+#\([^;]*?\)\s+u_core\b")
        self.assertRegex(core, r"ro_array\s+#\([^;]*?\)\s+u_array\b")
        self.assertRegex(core, r"puf_meas\s+#\([^;]*?\)\s+u_meas\b")
        sync = [a[4] for a in cmds(rec, "set_instance_assignment")
                if a[1] == "SYNCHRONIZER_IDENTIFICATION"]
        self.assertEqual(sync, ["*u_meas|halted_a_sync[*]", "*u_meas|halted_b_sync[*]",
                                "*u_meas|u_cnt_?|stop_sync[*]", "*sidik_avmm:sidik_?|tamper_s[*]"])
        self.assertIn("reg [1:0] tamper_s;", avmm)
        glob = {a[1]: a[2] for a in cmds(rec, "set_global_assignment") if len(a) == 3}
        self.assertEqual(glob["VERILOG_MACRO"], "CYCLONEV=1")
        self.assertEqual(glob["IGNORE_LCELL_BUFFERS"], "OFF")
        self.assertTrue(Path(glob["SDC_FILE"]).samefile(REL / "sidik_ro.sdc"))
        self.assertEqual(cmds(rec, "export_assignments"), [[]])

    def test_ro_sdc(self):
        rec = run_tcl(REL / "sidik_ro.sdc")
        clocks = {a[1]: a[-1] for a in cmds(rec, "create_clock")}
        self.assertEqual(set(clocks), {f"sidik_{i}_ro_{s}" for i in "ab" for s in "ab"})
        for name, target in clocks.items():
            inst, side = name.split("_")[1], name[-1]
            self.assertEqual(target, f"*sidik_{inst}|u_core|u_meas|u_cnt_{side}|g_div.pre*|clk")
        self.assertEqual(len(cmds(rec, "create_generated_clock")), 4)
        self.assertEqual(len(cmds(rec, "set_clock_groups")), 4)
        fp = cmds(rec, "set_false_path")
        self.assertIn(["-through", "*sidik_?|u_core|u_array|*"], fp)
        self.assertIn(["-to", "*sidik_?|tamper_s[0]"], fp)
        meas = read(RTL / "puf_meas.v")
        self.assertIn("begin : g_div", meas)
        self.assertIn("parameter integer PRESCALE_LOG2 = 1", meas)
        self.assertNotIn("PRESCALE_LOG2", re.findall(r"add_parameter\s+(\w+)",
                                                     read(REL / "sidik_hw.tcl")))

    def test_qsf_and_top_sdc(self):
        rec = run_tcl(REL / "sidik_release.qsf")
        top_name = [a[2] for a in cmds(rec, "set_global_assignment")
                    if a[1] == "TOP_LEVEL_ENTITY"][0]
        ports = module_ports(read(REL / "sidik_release_top.v"), top_name)
        pins = cmds(rec, "set_location_assignment")
        self.assertEqual(len({p[0] for p in pins}), len(pins), "duplicate pin")
        for _, _, port in pins:
            self.assertIn(re.sub(r"\[.*", "", port), ports)
        for a in cmds(rec, "set_global_assignment"):
            if a[1] in ("VERILOG_FILE", "SDC_FILE"):
                self.assertTrue((REL / a[2]).is_file(), a[2])
        rec = run_tcl(REL / "sidik_release_top.sdc")
        self.assertEqual({a[1] for a in cmds(rec, "create_clock")}, {"clk50", "altera_reserved_tck"})


@unittest.skipUnless(shutil.which("iverilog"), "iverilog not installed")
class TopCompileTest(unittest.TestCase):
    def test_top_compiles_with_component_files(self):
        srcs = [REL / "sidik_release_top.v", ROOT / "tb" / "common" / "release_sys_stub.v",
                ROOT / "tb" / "common" / "lcell_stub.v",
                ROOT / "tb" / "sidik_system" / "sidik_window.v", *hw_tcl_files()]
        r = subprocess.run(["iverilog", "-g2012", "-DCYCLONEV", "-Wall", "-o", os.devnull,
                            "-s", "sidik_release_top", *map(str, srcs)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


SYSCON_MOCK = r"""
# Mock of the System Console master service: master 0 is some other
# master (reads 0), master 1 sees the SIDIK window with MAGIC at 0x0, 0x100.
proc get_service_paths {type} { return {/m/other /m/sidik} }
proc claim_service {type path lib} { return $path }
proc close_service {type c} {}
array set mem {}
set mem(0) 0x53444B31
set mem(256) 0x53444B31
proc master_read_32 {m addr n} {
    if {$m ne "/m/sidik"} { return 0x00000000 }
    set a [expr {$addr}]
    if {[info exists ::mem($a)]} { return [format 0x%08x $::mem($a)] }
    return 0x00000000
}
proc master_write_32 {m addr v} {
    if {$m eq "/m/sidik"} { set ::mem([expr {$addr}]) $v }
}
set argv [list 0]
source [lindex $::script_argv 0]
"""


@unittest.skipUnless(shutil.which("tclsh"), "tclsh not installed (apt install tcl)")
class BridgeTest(unittest.TestCase):
    def test_bridge_against_mock(self):
        script = REL / "syscon" / "sidik_bridge.tcl"
        p = subprocess.Popen(["tclsh"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        try:
            p.stdin.write(f"set ::script_argv [list {{{script}}}]\n" + SYSCON_MOCK)
            p.stdin.close()
            line = p.stdout.readline()
            m = re.search(r"listening on 127\.0\.0\.1:(\d+)", line)
            self.assertTrue(m, line + p.stderr.read() if p.poll() is not None else line)
            port = int(m[1])
            bus = V.SocketBus(("127.0.0.1", port), timeout=10)
            for base in R.INSTANCE_OFFSET.values():
                V.Sidik(bus, base).check_magic()
            bus.write32(R.INSTANCE_OFFSET["B"] + R.TAU, 0x40)
            self.assertEqual(bus.read32(R.INSTANCE_OFFSET["B"] + R.TAU), 0x40)
            self.assertEqual(bus.read32(R.INSTANCE_OFFSET["A"] + R.TAU), 0)
            for bad in ("T 0", "R", "R xyz", "W 4", "R 123456789"):
                with self.assertRaises(V.VerifierError):
                    bus._req(bad)            # ERR, and the connection stays usable
            self.assertEqual(bus.read32(R.INSTANCE_OFFSET["A"]), R.MAGIC_VALUE)
            bus.close()
            self.assertEqual(V.host_port("127.0.0.1:2540"), ("127.0.0.1", 2540))
            self.assertEqual(V.host_port(":2540"), ("127.0.0.1", 2540))
        finally:
            p.kill()
            p.wait()


if __name__ == "__main__":
    unittest.main()
