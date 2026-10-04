# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""SIDIK verifier: enrollment, one-time challenge authentication, clone demo.

    verifier.py [transport] enroll     --instance A --db a.db [-n 16] [--tau 64]
    verifier.py [transport] auth       --instance A --db a.db
    verifier.py [transport] clone-demo [-n 4] [--tau 64] [--db-dir DIR]

Transports (one of):
    --mem [--bridge 0xFF200000] [--window 0x40000]
        /dev/mem on the DE10-Nano HPS: lightweight HPS-to-FPGA bridge, SIDIK
        window at bridge + window (fpga/release/add_to_ghrd.tcl), root only.
    --jtag HOST:PORT
        fpga/release/syscon/sidik_bridge.tcl in System Console on the host PC
        (JTAG to Avalon master, USB-Blaster II).
    --sim-socket PATH
        a running `sw/sidik_sim.py --socket PATH` (model of the two instances).
    --sim
        the same model in this process (state is lost at exit, so this is only
        useful for clone-demo).
Instance A is at window + 0x000, B at window + 0x100 (sw/sidik_regs.py).

Enrollment (trusted environment, once per device): ENROLL at TAU, read the
helper data (pair mask, syndromes, KCV) and the device ID, then run AUTH on
N fresh random 32-byte challenges and store the challenge-response pairs
(CRPs). The verifier never sees K. The device is CLEARed afterwards.

Authentication: write the stored helper data, RECONSTRUCT, check the ID,
take an unused CRP, mark it used and save the database *before* the
challenge goes to the device, run AUTH and compare the response in constant
time. Every challenge is used at most once; with none left the verifier
refuses (re-enroll). The device is CLEARed at the end of every session.

Clone demo: enroll A, then present A's helper data to B (a second PUF array
on the same FPGA, as a cloned chip carrying A's public data would). B cannot
reproduce A's key: reconstruction fails or the ID / response differ, and
the verifier rejects B; A is still accepted.

The database is a text file shared with sw/sidik_verifier.c:
    # sidik-verifier db v1
    id <64 hex>
    helper <18 words, 8 hex each>
    crp <challenge 64 hex> <response 64 hex> <0 unused | 1 used>
"""

import argparse
import hmac
import mmap
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import sidik_regs as R

LW_BRIDGE_BASE = 0xFF200000     # Cyclone V HPS lightweight HPS-to-FPGA bridge
DEFAULT_WINDOW = 0x40000        # SIDIK window inside the bridge (add_to_ghrd.tcl)
DB_HEADER = "# sidik-verifier db v1"
DEFAULT_TAU = 64                # counts, the model's tau (LOG2N = 14 release build)


class VerifierError(Exception):
    """Device or protocol error (not a rejection)."""


# ---- transports --------------------------------------------------------------------
# Each offers read32(offset), write32(offset, value) and pause() (called
# between STATUS polls); offsets are bytes inside the SIDIK window.

class MemBus:
    """/dev/mem mapping of the SIDIK window behind the lightweight bridge."""

    def __init__(self, bridge=LW_BRIDGE_BASE, window=DEFAULT_WINDOW, span=0x200):
        phys = bridge + window
        page = phys & ~(mmap.PAGESIZE - 1)
        self._delta = phys - page
        fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
        try:
            self._mm = mmap.mmap(fd, self._delta + span, mmap.MAP_SHARED,
                                 mmap.PROT_READ | mmap.PROT_WRITE, offset=page)
        finally:
            os.close(fd)
        self._mv = memoryview(self._mm).cast("I")   # 32-bit accesses

    def read32(self, offset):
        return self._mv[(self._delta + offset) // 4]

    def write32(self, offset, value):
        self._mv[(self._delta + offset) // 4] = value & 0xFFFFFFFF

    def pause(self):
        time.sleep(0.001)


class SocketBus:
    """Line-protocol client (see sidik_sim.py): a UNIX socket path for
    sidik_sim.py, or a (host, port) tuple for sidik_bridge.tcl (JTAG)."""

    def __init__(self, address, timeout=30.0):
        family = socket.AF_UNIX if isinstance(address, str) else socket.AF_INET
        self._sock = socket.socket(family, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)      # a stuck bridge raises, never hangs
        self._sock.connect(address)
        self._f = self._sock.makefile("rw")

    def _req(self, line):
        self._f.write(line + "\n")
        self._f.flush()
        reply = self._f.readline().strip()
        if not reply or reply == "ERR":
            raise VerifierError(f"simulator rejected {line!r}")
        return reply

    def read32(self, offset):
        return int(self._req(f"R {offset:x}"), 16)

    def write32(self, offset, value):
        self._req(f"W {offset:x} {value & 0xFFFFFFFF:x}")

    def tamper(self, offset):
        self._req(f"T {offset:x}")

    def pause(self):
        pass

    def close(self):
        self._f.close()
        self._sock.close()


# ---- one SIDIK instance ------------------------------------------------------------

def to_words(b):
    return [int.from_bytes(b[i:i + 4], "big") for i in range(0, len(b), 4)]


def to_bytes(words):
    return b"".join(w.to_bytes(4, "big") for w in words)


class Sidik:
    """Register access to the instance at `base` (byte offset) on `bus`."""

    def __init__(self, bus, base=0, max_polls=100_000):
        self.bus, self.base, self.max_polls = bus, base, max_polls

    def rd(self, off):
        return self.bus.read32(self.base + off)

    def wr(self, off, value):
        self.bus.write32(self.base + off, value)

    def rd_block(self, off, n):
        return [self.rd(off + 4 * i) for i in range(n)]

    def wr_block(self, off, words):
        for i, w in enumerate(words):
            self.wr(off + 4 * i, w)

    def status(self):
        return self.rd(R.STATUS)

    def check_magic(self):
        m = self.rd(R.MAGIC)
        if m != R.MAGIC_VALUE:
            raise VerifierError(f"no SIDIK at offset 0x{self.base:x} "
                                f"(MAGIC 0x{m:08x})")

    def command(self, bit):
        """Write CTRL and wait for completion; returns STATUS."""
        self.wr(R.CTRL, bit)
        for _ in range(self.max_polls):
            s = self.status()
            if s & R.TAMPERED:
                return s
            if not s & R.BUSY and s & R.DONE:
                return s
            self.bus.pause()
        raise VerifierError("timeout waiting for the device")

    def clear(self):
        self.wr(R.CTRL, R.CLEAR)

    def enroll(self, tau):
        """ENROLL -> (status, helper words (18), id bytes)."""
        self.wr(R.TAU, tau)
        s = self.command(R.ENROLL)
        if s & (R.ERR | R.TAMPERED) or not s & R.K_READY:
            return s, None, None
        return s, self.rd_block(R.HELPER, R.N_HELPER_WORDS), to_bytes(self.rd_block(R.ID, 8))

    def reconstruct(self, helper):
        """Write the helper data, RECONSTRUCT -> (status, id bytes or None)."""
        self.wr_block(R.HELPER, helper)
        s = self.command(R.RECONSTRUCT)
        if s & (R.ERR | R.RECON_FAIL | R.TAMPERED) or not s & R.K_READY:
            return s, None
        return s, to_bytes(self.rd_block(R.ID, 8))

    def auth(self, challenge):
        """AUTH on a 32-byte challenge -> (status, response bytes or None)."""
        self.wr_block(R.CHAL, to_words(challenge))
        s = self.command(R.AUTH)
        if s & (R.ERR | R.TAMPERED):
            return s, None
        return s, to_bytes(self.rd_block(R.RESP, 8))


# ---- database ----------------------------------------------------------------------

@dataclass
class Crp:
    challenge: bytes
    response: bytes
    used: bool = False


@dataclass
class Db:
    id: bytes
    helper: list
    crps: list = field(default_factory=list)

    def unused(self):
        return sum(not c.used for c in self.crps)

    def dumps(self):
        lines = [DB_HEADER, f"id {self.id.hex()}",
                 "helper " + " ".join(f"{w:08x}" for w in self.helper)]
        lines += [f"crp {c.challenge.hex()} {c.response.hex()} {int(c.used)}"
                  for c in self.crps]
        return "\n".join(lines) + "\n"

    @classmethod
    def loads(cls, text):
        lines = [ln.split() for ln in text.splitlines()]
        if not text.startswith(DB_HEADER):
            raise VerifierError("not a sidik-verifier v1 database")
        dev_id, helper, crps = None, None, []
        for parts in lines:
            if not parts or parts[0].startswith("#"):
                continue
            key, args = parts[0], parts[1:]
            if key == "id" and len(args) == 1 and len(args[0]) == 64:
                dev_id = bytes.fromhex(args[0])
            elif key == "helper" and len(args) == R.N_HELPER_WORDS:
                helper = [int(a, 16) for a in args]
            elif (key == "crp" and len(args) == 3 and len(args[0]) == 64
                  and len(args[1]) == 64 and args[2] in ("0", "1")):
                crps.append(Crp(bytes.fromhex(args[0]), bytes.fromhex(args[1]),
                                args[2] == "1"))
            else:
                raise VerifierError(f"bad database line: {' '.join(parts)}")
        if dev_id is None or helper is None:
            raise VerifierError("database lacks id or helper")
        return cls(dev_id, helper, crps)

    def save(self, path):
        """Atomic replace: a crash never leaves a used CRP marked unused."""
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w") as f:
            f.write(self.dumps())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    @classmethod
    def load(cls, path):
        return cls.loads(Path(path).read_text())


# ---- flows -------------------------------------------------------------------------

def enroll_device(dev, n_crp, tau=DEFAULT_TAU, randbytes=os.urandom):
    """Enroll the device and collect `n_crp` CRPs; returns a Db."""
    dev.check_magic()
    try:
        s, helper, dev_id = dev.enroll(tau)
        if helper is None:
            raise VerifierError(f"enrollment failed (STATUS 0x{s:02x})")
        db = Db(dev_id, helper)
        for _ in range(n_crp):
            chal = randbytes(32)
            s, resp = dev.auth(chal)
            if resp is None:
                raise VerifierError(f"AUTH failed during enrollment (STATUS 0x{s:02x})")
            db.crps.append(Crp(chal, resp))
        return db
    finally:
        dev.clear()


@dataclass
class AuthResult:
    accepted: bool
    reason: str


def authenticate(dev, db, save=lambda db: None):
    """One authentication session. `save(db)` persists the CRP use."""
    dev.check_magic()
    try:
        s, dev_id = dev.reconstruct(db.helper)
        if s & R.TAMPERED:
            return AuthResult(False, "device tampered")
        if dev_id is None:
            what = "reconstruction failed" if s & R.RECON_FAIL else "device error"
            return AuthResult(False, f"{what} (STATUS 0x{s:02x})")
        if not hmac.compare_digest(dev_id, db.id):
            return AuthResult(False, "ID mismatch")
        crp = next((c for c in db.crps if not c.used), None)
        if crp is None:
            return AuthResult(False, "no unused challenge left: re-enroll")
        crp.used = True
        save(db)            # burn the challenge before the device sees it
        s, resp = dev.auth(crp.challenge)
        if resp is None:
            return AuthResult(False, f"AUTH failed (STATUS 0x{s:02x})")
        if not hmac.compare_digest(resp, crp.response):
            return AuthResult(False, "response mismatch")
        return AuthResult(True, f"accepted ({db.unused()} challenges left)")
    finally:
        dev.clear()


def clone_demo(bus, n_crp=4, tau=DEFAULT_TAU, db_dir=None, log=print):
    """Enroll A, authenticate A, then B with A's helper data.

    Returns (result_a, result_b); the demo succeeds when A is accepted and
    B rejected.
    """
    a = Sidik(bus, R.INSTANCE_OFFSET["A"])
    b = Sidik(bus, R.INSTANCE_OFFSET["B"])
    db = enroll_device(a, n_crp, tau)
    log(f"enrolled A: ID {db.id.hex()[:16]}..., {len(db.crps)} CRPs")
    save = (lambda d: d.save(Path(db_dir) / "a.db")) if db_dir else (lambda d: None)
    save(db)
    ra = authenticate(a, db, save)
    log(f"A with A's helper data: {'ACCEPT' if ra.accepted else 'REJECT'} - {ra.reason}")
    rb = authenticate(b, db, save)
    log(f"B with A's helper data: {'ACCEPT' if rb.accepted else 'REJECT'} - {rb.reason}")
    return ra, rb


# ---- command line ------------------------------------------------------------------

def host_port(text):
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def open_bus(args):
    if args.jtag:
        return SocketBus(args.jtag)
    if args.sim_socket:
        return SocketBus(args.sim_socket)
    if args.sim:
        import sidik_sim
        return sidik_sim.SimBus()
    return MemBus(args.bridge, args.window)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="\n".join(__doc__.splitlines()[2:]))
    t = ap.add_mutually_exclusive_group(required=True)
    t.add_argument("--mem", action="store_true", help="/dev/mem via the LW bridge")
    t.add_argument("--jtag", metavar="HOST:PORT", type=host_port,
                   help="System Console bridge (fpga/release/syscon/sidik_bridge.tcl)")
    t.add_argument("--sim-socket", metavar="PATH", help="sidik_sim.py socket")
    t.add_argument("--sim", action="store_true", help="in-process model")
    ap.add_argument("--bridge", type=lambda x: int(x, 0), default=LW_BRIDGE_BASE)
    ap.add_argument("--window", type=lambda x: int(x, 0), default=DEFAULT_WINDOW)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("enroll")
    e.add_argument("--instance", choices=sorted(R.INSTANCE_OFFSET), default="A")
    e.add_argument("--db", required=True)
    e.add_argument("-n", type=int, default=16, help="CRPs to store")
    e.add_argument("--tau", type=int, default=DEFAULT_TAU)
    a = sub.add_parser("auth")
    a.add_argument("--instance", choices=sorted(R.INSTANCE_OFFSET), default="A")
    a.add_argument("--db", required=True)
    c = sub.add_parser("clone-demo")
    c.add_argument("-n", type=int, default=4)
    c.add_argument("--tau", type=int, default=DEFAULT_TAU)
    c.add_argument("--db-dir")
    args = ap.parse_args(argv)

    bus = None
    try:
        bus = open_bus(args)
        return run(bus, args)
    except (VerifierError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if hasattr(bus, "close"):
            bus.close()


def run(bus, args):
    if args.cmd == "enroll":
        db = enroll_device(Sidik(bus, R.INSTANCE_OFFSET[args.instance]), args.n, args.tau)
        db.save(args.db)
        print(f"enrolled {args.instance}: ID {db.id.hex()}, {len(db.crps)} CRPs -> {args.db}")
        return 0
    if args.cmd == "auth":
        db = Db.load(args.db)
        r = authenticate(Sidik(bus, R.INSTANCE_OFFSET[args.instance]), db,
                         lambda d: d.save(args.db))
        print(f"{'ACCEPT' if r.accepted else 'REJECT'}: {r.reason}")
        return 0 if r.accepted else 1
    ra, rb = clone_demo(bus, args.n, args.tau, args.db_dir)
    ok = ra.accepted and not rb.accepted
    print("clone demo:", "B rejected, A accepted" if ok else "UNEXPECTED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
