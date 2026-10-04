# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests of sw/verifier.py and sw/sidik_verifier.c against the device model
(sw/sidik_sim.py), and of the register constants against rtl/sidik_avmm.v.
The RTL itself runs the same verifier in tb/sidik_system (make test-sidik_system).
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import sidik_regs as R
import sidik_sim
import verifier as V

SW = Path(__file__).resolve().parent
ROOT = SW.parent


class SpyBus(sidik_sim.SimBus):
    """SimBus that records every challenge written to an instance."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.challenges = []
        self._chal = {}

    def write32(self, offset, value):
        base, word = offset & ~(R.WINDOW_BYTES - 1), (offset & (R.WINDOW_BYTES - 1)) // 4
        if R.W_CHAL <= word < R.W_CHAL + 8:
            self._chal.setdefault(base, [0] * 8)[word - R.W_CHAL] = value
        if word == R.W_CTRL and value & R.AUTH:
            self.challenges.append(V.to_bytes(self._chal.get(base, [0] * 8)))
        super().write32(offset, value)


def counter_bytes():
    n = 0

    def rb(k):
        nonlocal n
        n += 1
        return n.to_bytes(k, "big")
    return rb


class TestVerifierFlows(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bus = SpyBus(seed_a=11, seed_b=12)
        cls.a = V.Sidik(cls.bus, R.INSTANCE_OFFSET["A"])
        cls.b = V.Sidik(cls.bus, R.INSTANCE_OFFSET["B"])
        cls.db_a = V.enroll_device(cls.a, 6, V.DEFAULT_TAU)

    def fresh_db(self):
        return V.Db.loads(self.db_a.dumps())

    def test_enrollment_contents(self):
        db = self.db_a
        self.assertEqual(len(db.helper), R.N_HELPER_WORDS)
        self.assertEqual(sum(bin(w).count("1") for w in db.helper[:16]), 216)
        self.assertEqual(len(db.crps), 6)
        self.assertEqual(len({c.challenge for c in db.crps}), 6)
        self.assertFalse(any(c.used for c in db.crps))
        # the device is CLEARed after enrollment: no K, nothing readable
        self.assertEqual(self.a.status(), 0)
        self.assertEqual(self.a.rd_block(R.ID, 8), [0] * 8)

    def test_accept_and_one_time_challenges(self):
        db = self.fresh_db()
        saved = []
        self.bus.challenges.clear()
        for i in range(len(db.crps)):
            r = V.authenticate(self.a, db, lambda d: saved.append(d.dumps()))
            self.assertTrue(r.accepted, r.reason)
            self.assertEqual(db.unused(), len(db.crps) - i - 1)
            self.assertEqual(self.a.status(), 0, "session not CLEARed")
        # each stored challenge went to the device exactly once, in order
        self.assertEqual(self.bus.challenges, [c.challenge for c in db.crps])
        # each save happened with the CRP already marked used
        self.assertEqual([s.count(" 1\n") for s in saved], list(range(1, len(db.crps) + 1)))
        r = V.authenticate(self.a, db)
        self.assertFalse(r.accepted)
        self.assertIn("no unused challenge", r.reason)
        self.assertEqual(len(self.bus.challenges), len(db.crps))

    def test_clone_rejected(self):
        db = self.fresh_db()
        self.bus.challenges.clear()
        r = V.authenticate(self.b, db)
        self.assertFalse(r.accepted)
        self.assertIn("reconstruction failed", r.reason)
        self.assertEqual(db.unused(), len(db.crps), "a rejected clone burnt a challenge")
        self.assertEqual(self.bus.challenges, [], "a challenge reached the clone")
        self.assertTrue(V.authenticate(self.a, db).accepted)

    def test_clone_demo(self):
        lines = []
        ra, rb = V.clone_demo(SpyBus(seed_a=21, seed_b=22), 2, log=lines.append)
        self.assertTrue(ra.accepted, ra.reason)
        self.assertFalse(rb.accepted)
        self.assertEqual(len(lines), 3)

    def test_wrong_response_rejected_and_burnt(self):
        db = self.fresh_db()
        db.crps[0].response = bytes(32)
        r = V.authenticate(self.a, db)
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "response mismatch")
        self.assertTrue(db.crps[0].used)
        self.assertTrue(V.authenticate(self.a, db).accepted)

    def test_wrong_id_rejected(self):
        db = self.fresh_db()
        db.id = bytes(32)
        r = V.authenticate(self.a, db)
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "ID mismatch")
        self.assertEqual(db.unused(), len(db.crps))

    def test_corrupt_helper_rejected(self):
        db = self.fresh_db()
        db.helper[17] ^= 1                  # KCV
        self.assertFalse(V.authenticate(self.a, db).accepted)
        db = self.fresh_db()
        db.helper[0] ^= 0xFF                # mask no longer selects 216 pairs
        self.assertFalse(V.authenticate(self.a, db).accepted)

    def test_tamper(self):
        bus = SpyBus(seed_a=11, seed_b=12)
        a = V.Sidik(bus, R.INSTANCE_OFFSET["A"])
        db = self.fresh_db()
        bus.tamper(R.INSTANCE_OFFSET["A"])
        r = V.authenticate(a, db)
        self.assertFalse(r.accepted)
        self.assertEqual(r.reason, "device tampered")
        with self.assertRaises(V.VerifierError):
            V.enroll_device(a, 1)
        bus.devices[R.INSTANCE_OFFSET["A"]].reset()
        self.assertTrue(V.authenticate(a, db).accepted)

    def test_no_device(self):
        with self.assertRaises(V.VerifierError):
            V.authenticate(V.Sidik(self.bus, 0x200), self.fresh_db())


class TestDatabase(unittest.TestCase):
    def test_roundtrip_and_errors(self):
        db = V.Db(bytes(range(32)), list(range(18)),
                  [V.Crp(bytes([1]) * 32, bytes([2]) * 32, False),
                   V.Crp(bytes([3]) * 32, bytes([4]) * 32, True)])
        self.assertEqual(V.Db.loads(db.dumps()), db)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.db"
            db.save(p)
            self.assertEqual(V.Db.load(p), db)
            self.assertEqual(os.listdir(d), ["x.db"])
        text = db.dumps()
        for bad in (text.replace("v1", "v2"), text.replace(" 1\n", " 2\n"),
                    text.replace("helper 00000000 ", "helper "),
                    "\n".join(ln for ln in text.splitlines() if not ln.startswith("id"))):
            with self.assertRaises(V.VerifierError):
                V.Db.loads(bad)


def rtl_localparams():
    src = (ROOT / "rtl" / "sidik_avmm.v").read_text()
    out = {m[1]: int(m[2]) for m in re.finditer(r"\b(A_\w+)\s*=\s*6'd(\d+)", src)}
    out["MAGIC"] = int(re.search(r"MAGIC = 32'h([0-9A-Fa-f]+)", src)[1], 16)
    status = re.search(r"wire \[31:0\] status = \{26'd0,([^}]*)\}", src)[1]
    out["status"] = [s.strip() for s in status.split(",")]
    ctrl = dict(re.findall(r"wire cmd_(\w+)\s*=\s*wr_ctrl && avs_writedata\[(\d)\]", src))
    out["ctrl"] = {k: 1 << int(v) for k, v in ctrl.items()}
    return out


def c_defines():
    src = (SW / "sidik_regs.h").read_text()
    return {m[1]: int(m[2], 0) for m in
            re.finditer(r"#define SIDIK_(\w+)\s+(0x[0-9A-Fa-f]+|\d+)u?", src)}


class TestRegisterConstants(unittest.TestCase):
    def test_python_matches_rtl(self):
        rtl = rtl_localparams()
        self.assertEqual(R.MAGIC_VALUE, rtl["MAGIC"])
        for name, word in (("MAGIC", R.W_MAGIC), ("CTRL", R.W_CTRL), ("STATUS", R.W_STATUS),
                           ("TAU", R.W_TAU), ("HELPER", R.W_HELPER), ("SYN", R.W_SYN),
                           ("KCV", R.W_KCV), ("CHAL", R.W_CHAL), ("RESP", R.W_RESP),
                           ("ID", R.W_ID)):
            self.assertEqual(rtl["A_" + name], word, name)
        self.assertEqual(R.W_HELPER + R.N_HELPER_WORDS, R.W_KCV + 1)
        # STATUS concatenation is MSB first: TAMPERED ... BUSY
        self.assertEqual(rtl["status"], ["tampered_q", "rfail_q", "cr_k_valid",
                                         "err_q | fault_q", "done_q", "busy"])
        self.assertEqual([R.BUSY, R.DONE, R.ERR, R.K_READY, R.RECON_FAIL, R.TAMPERED],
                         [1, 2, 4, 8, 16, 32])
        self.assertEqual(rtl["ctrl"], {"enroll": R.ENROLL, "recon": R.RECONSTRUCT,
                                       "auth": R.AUTH, "clear": R.CLEAR})
        self.assertEqual(R.WINDOW_BYTES, 4 * 64)   # 6-bit word address

    def test_c_header_matches_python(self):
        c = c_defines()
        expect = {"MAGIC_VALUE": R.MAGIC_VALUE, "MAGIC": R.MAGIC, "CTRL": R.CTRL,
                  "STATUS": R.STATUS, "TAU": R.TAU, "HELPER": R.HELPER, "CHAL": R.CHAL,
                  "RESP": R.RESP, "ID": R.ID, "N_HELPER_WORDS": R.N_HELPER_WORDS,
                  "WINDOW_BYTES": R.WINDOW_BYTES, "ENROLL": R.ENROLL,
                  "RECONSTRUCT": R.RECONSTRUCT, "AUTH": R.AUTH, "CLEAR": R.CLEAR,
                  "BUSY": R.BUSY, "DONE": R.DONE, "ERR": R.ERR, "K_READY": R.K_READY,
                  "RECON_FAIL": R.RECON_FAIL, "TAMPERED": R.TAMPERED,
                  "INSTANCE_A": R.INSTANCE_OFFSET["A"], "INSTANCE_B": R.INSTANCE_OFFSET["B"]}
        self.assertEqual(c, expect)

    def test_release_addresses_match_fpga(self):
        """fpga/release places sidik_a / sidik_b at the instance offsets."""
        rel = ROOT / "fpga" / "release"
        for tcl in ("release_sys.tcl", "add_to_ghrd.tcl"):
            src = (rel / tcl).read_text(encoding="utf-8")
            for inst, off in R.INSTANCE_OFFSET.items():
                self.assertRegex(src, rf"sidik_{inst.lower()}_offset\s+0x{off:03x}\b", tcl)
        src = (rel / "add_to_ghrd.tcl").read_text(encoding="utf-8")
        self.assertIn(f"0x{V.DEFAULT_WINDOW:x}", src)


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class TestCVerifier(unittest.TestCase):
    """sidik_verifier.c against sidik_sim.py over the socket, sharing the
    database with verifier.py."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        cls.exe = d / "sidik_verifier"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-std=gnu11",
                        "-o", str(cls.exe), str(SW / "sidik_verifier.c")], check=True)
        cls.sock = str(d / "sim.sock")
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT / "model"), str(SW)]))
        cls.server = subprocess.Popen(
            [sys.executable, str(SW / "sidik_sim.py"), "--socket", cls.sock,
             "--seed-a", "31", "--seed-b", "32"], env=env, stdout=subprocess.DEVNULL)
        for _ in range(200):
            if os.path.exists(cls.sock):
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("sidik_sim.py did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.terminate()
        cls.server.wait()
        cls.tmp.cleanup()

    def c(self, *args):
        return subprocess.run([str(self.exe), "-s", self.sock, *args],
                              capture_output=True, text=True)

    def py(self, *args):
        return V.main(["--sim-socket", self.sock, *args])

    def test_c_and_python_share_the_database(self):
        db = Path(self.tmp.name) / "a.db"
        r = self.c("enroll", "-i", "A", "-d", str(db), "-n", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(V.Db.load(db).crps), 3)
        r = self.c("auth", "-i", "A", "-d", str(db))
        self.assertEqual((r.returncode, r.stdout.split(":")[0]), (0, "ACCEPT"), r.stdout)
        self.assertEqual(self.py("auth", "--instance", "A", "--db", str(db)), 0)
        r = self.c("auth", "-i", "B", "-d", str(db))           # clone
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("reconstruction failed", r.stdout)
        self.assertEqual(self.c("auth", "-i", "A", "-d", str(db)).returncode, 0)
        r = self.c("auth", "-i", "A", "-d", str(db))           # all 3 used
        self.assertEqual(r.returncode, 1)
        self.assertIn("no unused challenge", r.stdout)
        self.assertTrue(all(c.used for c in V.Db.load(db).crps))

    def test_python_enrolls_c_authenticates(self):
        db = Path(self.tmp.name) / "b.db"
        self.assertEqual(self.py("enroll", "--instance", "B", "--db", str(db), "-n", "2"), 0)
        self.assertEqual(self.c("auth", "-i", "B", "-d", str(db)).returncode, 0)
        d = V.Db.load(db)
        self.assertEqual([c.used for c in d.crps], [True, False])
        d.crps[1].response = bytes(32)
        d.save(db)
        r = self.c("auth", "-i", "B", "-d", str(db))
        self.assertEqual(r.returncode, 1)
        self.assertIn("response mismatch", r.stdout)

    def test_clone_demo(self):
        r = self.c("clone-demo", "-n", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("B with A's helper data: REJECT", r.stdout)

    def test_bad_input(self):
        bad = Path(self.tmp.name) / "bad.db"
        bad.write_text("# sidik-verifier db v1\nid 00\n")
        self.assertEqual(self.c("auth", "-d", str(bad)).returncode, 2)
        self.assertEqual(self.c("auth").returncode, 2)
        self.assertEqual(subprocess.run([str(self.exe), "-s", "/nonexistent", "auth", "-d", "x"],
                                        capture_output=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
