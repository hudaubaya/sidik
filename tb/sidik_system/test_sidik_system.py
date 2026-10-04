# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests of the combined fpga/release system (tb.v: two sidik_avmm,
larik A at 0x000 and larik B at 0x100, behind the address decoder of
sidik_window.v, shared KEY0 tamper), driven by the verifier code itself.

sw/verifier.py runs unchanged in a cocotb.external thread; its bus is
RtlBus below (each read32 / write32 is a bus cycle on tb.v). The C verifier
(sw/sidik_verifier.c) talks to the same RTL through sidik_sim.serve() on a
UNIX socket.

- Address decoder: MAGIC at both bases, nothing outside them, a write to
  one instance never reaches the other; bases equal to fpga/release.
- Verifier on the RTL: enrollment (helper data, ID, CRPs), authentication
  with one-time challenges, refusal when they run out.
- Clone demo: helper data of A applied to B -> rejected, no challenge
  reaches B; A accepted. The same with the C verifier.
- KEY0: erases K and every buffer of both instances within 3 clock edges,
  TAMPERED on both, verifier rejects; after rst A authenticates again.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import cocotb
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tb" / "sidik_avmm"))
import test_sidik_avmm as avmm  # noqa: E402  (secret_state of one instance)

import sidik_regs as R  # noqa: E402
import sidik_sim  # noqa: E402
import verifier as V  # noqa: E402

TAU = 1                 # counts, as tb/sidik_avmm (LOG2N = 8, N_ENROLL = 4)
A, B = R.INSTANCE_OFFSET["A"], R.INSTANCE_OFFSET["B"]
STATE = {}              # verifier database of A, shared by the tests


class RtlBus:
    """sw/verifier.py transport onto tb.v. Called from cocotb.external
    threads; every access is one bus transaction in simulation time."""

    def __init__(self, dut, pause_cycles=512):
        self.dut = dut
        self.pause_cycles = pause_cycles
        self.reads, self.writes = [], []        # (offset, value) log

    @cocotb.function
    async def read32(self, offset):
        v = await bus_read(self.dut, offset)
        self.reads.append((offset, v))
        return v

    @cocotb.function
    async def write32(self, offset, value):
        self.writes.append((offset, value))
        await bus_write(self.dut, offset, value)

    @cocotb.function
    async def pause(self):
        await ClockCycles(self.dut.clk, self.pause_cycles)

    def challenges_to(self, base):
        """Challenges sent with AUTH to the instance at `base`."""
        out, chal = [], [0] * 8
        for off, v in self.writes:
            if base + R.CHAL <= off < base + R.CHAL + 32:
                chal[(off - base - R.CHAL) // 4] = v
            if off == base + R.CTRL and v & R.AUTH:
                out.append(V.to_bytes(chal))
        return out


async def bus_write(dut, offset, value):
    dut.address.value = offset
    dut.writedata.value = value
    dut.write.value = 1
    await RisingEdge(dut.clk)
    dut.write.value = 0


async def bus_read(dut, offset):
    dut.address.value = offset
    dut.read.value = 1
    await RisingEdge(dut.clk)
    dut.read.value = 0
    await ReadOnly()
    assert int(dut.readdatavalid.value) == 1
    v = int(dut.readdata.value)
    await RisingEdge(dut.clk)
    return v


async def reset(dut):
    dut.key0.value = 1
    dut.rst.value = 1
    await ClockCycles(dut.clk, 4)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


def inst(dut, name):
    return getattr(dut.u_win, f"sidik_{name.lower()}")


async def ensure_enrolled(dut, bus):
    if "db" not in STATE:
        await reset(dut)
        STATE["db"] = await cocotb.external(V.enroll_device)(V.Sidik(bus, A), 4, TAU)
    return V.Db.loads(STATE["db"].dumps())


def check(failures, cond, msg):
    if not cond:
        failures.append(msg)


# ---- tests ---------------------------------------------------------------------------

@cocotb.test()
async def test_address_decoder(dut):
    """MAGIC at A and B only; writes reach exactly one instance."""
    await reset(dut)
    failures = []
    for off in range(0, 0x400, 4):
        v = await bus_read(dut, off)
        expect = R.MAGIC_VALUE if off in (A, B) else None
        if expect is not None:
            check(failures, v == expect, f"MAGIC at {off:#x}: {v:#x}")
        elif off >= 0x200:
            check(failures, v == 0, f"{off:#x} outside the window reads {v:#x}")
    # TAU and CHAL are read-write: write one instance, read both
    await bus_write(dut, A + R.TAU, 0x1234)
    await bus_write(dut, B + R.TAU, 0x0042)
    await bus_write(dut, B + R.CHAL + 8, 0xDEADBEEF)
    await bus_write(dut, 0x200 + R.TAU, 0x7777)            # no slave
    check(failures, await bus_read(dut, A + R.TAU) == 0x1234, "A.TAU")
    check(failures, await bus_read(dut, B + R.TAU) == 0x0042, "B.TAU")
    check(failures, await bus_read(dut, A + R.CHAL + 8) == 0, "B's CHAL write reached A")
    check(failures, await bus_read(dut, B + R.CHAL + 8) == 0xDEADBEEF, "B.CHAL")
    check(failures, int(inst(dut, "a").tau_q.value) == 0x1234, "A.tau_q")
    # word decoding: A + 4*w addresses word w of A
    for w in (R.W_TAU, R.W_CHAL + 2):
        check(failures, (A + 4 * w) >> 2 & 63 == w, f"word {w}")
    # the decoder bases are those of fpga/release and sw/sidik_regs.py
    for tcl in ("release_sys.tcl", "add_to_ghrd.tcl"):
        src = (ROOT / "fpga" / "release" / tcl).read_text(encoding="utf-8")
        for name, base in (("a", A), ("b", B)):
            m = re.search(rf"set sidik_{name}_offset\s+(0x[0-9a-fA-F]+)", src)
            check(failures, m and int(m[1], 16) == base, f"{tcl}: sidik_{name} base")
    check(failures, int(dut.u_win.BASE_A.value) == A, "BASE_A")
    check(failures, int(dut.u_win.BASE_B.value) == B, "BASE_B")
    avmm.finish(dut, failures)


@cocotb.test()
async def test_verifier_one_time_challenges(dut):
    """sw/verifier.py on the RTL: enroll A, accept, every challenge once."""
    bus = RtlBus(dut)
    db = await ensure_enrolled(dut, bus)
    bus.writes.clear()                  # count only the challenges sent from here
    failures = []
    check(failures, len(db.crps) == 4, "CRPs")
    check(failures, sum(bin(w).count("1") for w in db.helper[:16]) == 216, "mask weight")
    await ClockCycles(dut.clk, 2)       # CLEAR takes effect after the write edge
    check(failures, int(inst(dut, "a").u_crypto.k_valid.value) == 0, "K left after enroll")
    # the stored responses are HMAC(K, challenge) of A's K (model check)
    saves = []
    for i in range(len(db.crps)):
        r = await cocotb.external(V.authenticate)(V.Sidik(bus, A), db,
                                                  lambda d: saves.append(d.unused()))
        check(failures, r.accepted, f"auth {i}: {r.reason}")
        await ClockCycles(dut.clk, 2)
        check(failures, int(inst(dut, "a").u_crypto.k_valid.value) == 0,
              f"auth {i}: session not cleared")
    check(failures, saves == [3, 2, 1, 0], f"CRP burnt before AUTH: {saves}")
    check(failures, bus.challenges_to(A) == [c.challenge for c in db.crps],
          "each challenge sent exactly once, in order")
    r = await cocotb.external(V.authenticate)(V.Sidik(bus, A), db)
    check(failures, not r.accepted and "no unused" in r.reason, f"exhausted: {r.reason}")
    check(failures, len(bus.challenges_to(A)) == len(db.crps), "challenge reused")
    avmm.finish(dut, failures)


@cocotb.test()
async def test_clone_demo(dut):
    """Helper data of A applied to B: B rejected, nothing sent to B; A accepted."""
    bus = RtlBus(dut)
    db = await ensure_enrolled(dut, bus)
    bus.writes.clear()
    failures = []
    rb = await cocotb.external(V.authenticate)(V.Sidik(bus, B), db)
    check(failures, not rb.accepted, f"clone accepted: {rb.reason}")
    dut._log.info(f"B with A's helper data: {rb.reason}")
    check(failures, db.unused() == len(db.crps), "the clone burnt a challenge")
    check(failures, bus.challenges_to(B) == [], "a challenge reached B")
    await ClockCycles(dut.clk, 2)
    check(failures, int(inst(dut, "b").u_crypto.k_valid.value) == 0, "B left with a K")
    ra = await cocotb.external(V.authenticate)(V.Sidik(bus, A), db)
    check(failures, ra.accepted, f"A after the clone attempt: {ra.reason}")
    # The whole demo as the CLI runs it, on fresh enrollments of A.
    lines = []
    ra, rb = await cocotb.external(V.clone_demo)(bus, 1, TAU, None, lines.append)
    check(failures, ra.accepted and not rb.accepted, f"clone_demo: {lines}")
    avmm.finish(dut, failures)


@cocotb.test(skip=shutil.which("gcc") is None)
async def test_c_verifier(dut):
    """sw/sidik_verifier.c on the RTL (UNIX socket): A accepted, clone B rejected."""
    bus = RtlBus(dut)
    db = await ensure_enrolled(dut, bus)
    failures = []
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        exe = d / "sidik_verifier"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-std=gnu11",
                        "-o", str(exe), str(ROOT / "sw" / "sidik_verifier.c")], check=True)
        db.save(d / "a.db")
        sock = str(d / "rtl.sock")
        for instance, expect in (("A", 0), ("B", 1)):
            procs = []

            def launch():
                procs.append(subprocess.Popen(
                    [str(exe), "-s", sock, "auth", "-i", instance, "-d", str(d / "a.db")],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
            await cocotb.external(sidik_sim.serve)(bus, sock, 1, launch)
            out, _ = procs[0].communicate(timeout=60)
            dut._log.info(f"C verifier, {instance} with A's helper data: {out.strip()}")
            check(failures, procs[0].returncode == expect, f"C auth {instance}: {out}")
        used = [c.used for c in V.Db.load(d / "a.db").crps]
        check(failures, used == [True, False, False, False], f"C database: {used}")
    avmm.finish(dut, failures)


@cocotb.test()
async def test_key0_tamper(dut):
    """KEY0 erases both instances within 3 edges; TAMPERED; rejected until rst."""
    bus = RtlBus(dut)
    db = await ensure_enrolled(dut, bus)
    failures = []
    # A holds K (reconstructed), B holds A's helper data and a challenge
    s, _ = await cocotb.external(V.Sidik(bus, A).reconstruct)(db.helper)
    check(failures, s & R.K_READY, f"A STATUS {s:#x}")
    for i, w in enumerate(db.helper):
        await bus_write(dut, B + R.HELPER + 4 * i, w)
    await bus_write(dut, B + R.CHAL, 0x01020304)
    await bus_write(dut, B + R.CTRL, R.RECONSTRUCT)        # B busy measuring
    await ClockCycles(dut.clk, 3000)
    await FallingEdge(dut.clk)
    shims = {n: SimpleNamespace(u_dut=inst(dut, n)) for n in "ab"}
    before = {n: avmm.nonzero_state(s) for n, s in shims.items()}
    dut.key0.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    await ReadOnly()
    for n, s in shims.items():
        left = avmm.nonzero_state(s)
        check(failures, not left, f"{n}: not erased after 3 edges: {left}")
        check(failures, before[n], f"{n}: nothing to erase (test too weak)")
    await Timer(1, units="ns")
    dut.key0.value = 1
    await ClockCycles(dut.clk, 10)
    for base in (A, B):
        st = await bus_read(dut, base + R.STATUS)
        check(failures, st & R.TAMPERED and not st & R.K_READY, f"{base:#x} STATUS {st:#x}")
    r = await cocotb.external(V.authenticate)(V.Sidik(bus, A), db)
    check(failures, not r.accepted and r.reason == "device tampered", r.reason)
    check(failures, db.unused() == len(db.crps), "a challenge burnt on a tampered device")
    await reset(dut)
    r = await cocotb.external(V.authenticate)(V.Sidik(bus, A), db)
    check(failures, r.accepted, f"after rst: {r.reason}")
    avmm.finish(dut, failures)
