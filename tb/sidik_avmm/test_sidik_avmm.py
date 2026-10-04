# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/sidik_avmm.v (release build, or CHAR_BUILD with
+BUILD=char).

- Full flow: ENROLL -> HELPER out, RECONSTRUCT with HELPER written back ->
  same K and ID, AUTH -> RESP = HMAC(K, CHAL) (Python hmac).
- Address scan: every one of the 64 word addresses is read after each step.
  No word may equal any 32-bit window of K, of the key bits or of the
  inner HMAC state, nor any raw counter value seen during the run. In
  CHAR_BUILD the scan must find the raw counters (positive control) and
  still not K.
- Tamper: tamper_n falls at random cycles (during reconstruction races, the
  crypto, AUTH, idle). Three rising edges later every key, buffer, Shaman
  and counter register must be 0; TAMPERED stays set until rst and
  commands are refused meanwhile.
- Illegal state deposited into the bus FSM -> CLEAR (all state erased, ERR);
  into fuzzy_ext / sidik_crypto -> their own buffers / K erased.
- CLEAR command erases the same state.
"""

import hashlib
import hmac

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge, Timer

MAGIC, CTRL, STATUS, TAU = 0, 1, 2, 3
HELPER, SYN, KCV, CHAL, RESP, ID = 4, 20, 21, 22, 30, 38
RAW_PAIR, RAW_CTRL, RAW_A, RAW_B, RAW_D = 48, 49, 50, 51, 52
ENROLL, RECON, AUTH, CLEAR = 1, 2, 4, 8
BUSY, DONE, ERR, K_READY, RECON_FAIL, TAMPERED = (1 << i for i in range(6))
TAU_COUNTS = 1

SHAMAN = [
    "shapi.apibuf", "shapi.blockproc_bp_initdat", "shapi.resultbyteOut",
    *[f"shapi.blockproc.hbuf{i}" for i in range(8)],
    *[f"shapi.blockproc.bp_{c}" for c in "abcdefgh"],
    "shapi.blockproc.t1.wt.wt_buf",
]

STATE = {}   # helper data, K and ID from the first enrollment (shared by tests)


def char_build():
    return cocotb.plusargs.get("BUILD") == "char"


def h(root, path):
    for part in path.split("."):
        root = getattr(root, part)
    return root


def val(root, path):
    return int(h(root, path).value)


# ---- bus ---------------------------------------------------------------------

async def write(dut, addr, data):
    dut.avs_address.value = addr
    dut.avs_writedata.value = data
    dut.avs_write.value = 1
    await RisingEdge(dut.clk)
    dut.avs_write.value = 0


async def read(dut, addr):
    dut.avs_address.value = addr
    dut.avs_read.value = 1
    await RisingEdge(dut.clk)
    dut.avs_read.value = 0
    await ReadOnly()
    v = int(dut.avs_readdata.value)
    await RisingEdge(dut.clk)
    return v


async def reset(dut):
    dut.tamper_n.value = 1
    dut.rst.value = 1
    await ClockCycles(dut.clk, 4)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def wait_idle(dut, limit=2_000_000):
    """Wait until BUSY drops (polls the FSM directly, every 64 cycles)."""
    for _ in range(limit // 64):
        await ClockCycles(dut.clk, 64)
        if int(dut.u_dut.state.value) == 0:
            await ClockCycles(dut.clk, 2)
            return await read(dut, STATUS)
    raise AssertionError("timeout waiting for idle")


async def command(dut, bits):
    await write(dut, CTRL, bits)
    return await wait_idle(dut)


async def read_words(dut, base, n):
    return [await read(dut, base + i) for i in range(n)]


def words_to_bytes(words):
    return b"".join(w.to_bytes(4, "big") for w in words)


# ---- secrets observed during the run ------------------------------------------

class Secrets:
    def __init__(self):
        self.values = []        # (bits, int) of K, key bits, intermediate digests
        self.counts = set()     # raw counter values

    def add(self, nbits, v):
        if v:
            self.values.append((nbits, v))

    def windows(self):
        out = set()
        for n, v in self.values:
            for off in range(0, n - 31):
                w = (v >> off) & 0xFFFFFFFF
                if w:
                    out.add(w)
        return out


async def watch_races(dut, secrets):
    """Record every raw count the fuzzy extractor receives."""
    while True:
        await RisingEdge(dut.u_dut.fe_meas_ack)
        await ReadOnly()
        secrets.counts.add(int(dut.u_dut.core_count_a.value))
        secrets.counts.add(int(dut.u_dut.core_count_b.value))


async def watch_keys(dut, secrets):
    """Record the key bits and K whenever they exist."""
    while True:
        await RisingEdge(dut.u_dut.fe_key_valid)
        await ReadOnly()
        secrets.add(216, int(dut.u_dut.fe_key.value))
        await RisingEdge(dut.u_dut.u_crypto.done)
        await ReadOnly()
        secrets.add(256, int(dut.u_dut.u_crypto.k_q.value))


async def scan(dut, secrets, failures, what, expect_raw=False):
    words = [await read(dut, a) for a in range(64)]
    wins = secrets.windows()
    for a, w in enumerate(words):
        if w and w in wins:
            failures.append(f"{what}: address {a} reads a window of a secret ({w:#010x})")
    raw_hits = [a for a, w in enumerate(words) if w in secrets.counts]
    if expect_raw and not raw_hits:
        failures.append(f"{what}: CHAR_BUILD scan found no raw counter")
    if not expect_raw and raw_hits:
        failures.append(f"{what}: raw counter value readable at {raw_hits}")
    return words


def k_of(dut):
    return int(dut.u_dut.u_crypto.k_q.value).to_bytes(32, "big")


# ---- erased-state check ----------------------------------------------------------

def secret_state(dut):
    d = dut.u_dut
    regs = {
        "crypto.k_q": d.u_crypto.k_q, "crypto.k_valid": d.u_crypto.k_valid,
        "crypto.inner_q": d.u_crypto.inner_q, "crypto.dig": d.u_crypto.dig,
        "crypto.result": d.u_crypto.result,
        "fe.key_q": d.u_fe.key_q, "fe.sign_q": d.u_fe.sign_q, "fe.pass_q": d.u_fe.pass_q,
        "fe.acc": d.u_fe.acc, "fe.votes": d.u_fe.votes, "fe.helper_mask": d.u_fe.helper_mask,
        "resp_q": d.resp_q, "id_q": d.id_q, "chal_q": d.chal_q, "kcv_q": d.kcv_q,
        "helper_mask_q": d.helper_mask_q, "helper_syn_q": d.helper_syn_q,
        "fe_meas_delta": d.fe_meas_delta,
        "core.count_a": d.u_core.u_meas.count_a, "core.count_b": d.u_core.u_meas.count_b,
        "core.mag": d.u_core.u_meas.mag,
    }
    for p in SHAMAN:
        regs["shaman." + p] = h(d.u_crypto.u_shaman, p)
    return regs


def nonzero_state(dut, only=None):
    return sorted(n for n, s in secret_state(dut).items()
                  if (only is None or n.startswith(only)) and int(s.value) != 0)


def finish(dut, failures):
    for f in failures[:10]:
        dut._log.error(f)
    assert not failures, f"{len(failures)} failures"


# ---- flows ---------------------------------------------------------------------------

async def enroll(dut):
    await write(dut, TAU, TAU_COUNTS)
    st = await command(dut, ENROLL)
    assert st & DONE and not st & ERR and st & K_READY, f"enrollment STATUS {st:#x}"
    STATE["helper"] = await read_words(dut, HELPER, 18)
    STATE["k"] = k_of(dut)
    STATE["id"] = words_to_bytes(await read_words(dut, ID, 8))
    return st


async def reconstruct(dut, helper=None):
    for i, w in enumerate(helper or STATE["helper"]):
        await write(dut, HELPER + i, w)
    return await command(dut, RECON)


async def ensure_enrolled(dut):
    if "helper" not in STATE:
        await reset(dut)
        await enroll(dut)


# ---- tests -----------------------------------------------------------------------------

@cocotb.test()
async def test_flow_and_address_scan(dut):
    """ENROLL / RECONSTRUCT / AUTH work; no address reads K or raw data."""
    await reset(dut)
    secrets = Secrets()
    cocotb.start_soon(watch_races(dut, secrets))
    cocotb.start_soon(watch_keys(dut, secrets))
    failures = []
    assert await read(dut, MAGIC) == 0x53444B31

    await enroll(dut)
    k = STATE["k"]
    if STATE["id"] != hmac.new(k, b"SIDIK-ID", hashlib.sha256).digest():
        failures.append("ID after enrollment != HMAC(K, 'SIDIK-ID')")
    kcv = hmac.new(k, b"SIDIK-CHK", hashlib.sha256).digest()[:4]
    if STATE["helper"][17] != int.from_bytes(kcv, "big"):
        failures.append("KCV in HELPER != HMAC(K, 'SIDIK-CHK')[:4]")
    if bin(int.from_bytes(words_to_bytes(STATE["helper"][:16][::-1]), "big")).count("1") != 216:
        failures.append("helper mask does not select 216 pairs")
    await scan(dut, secrets, failures, "after ENROLL")

    await write(dut, CTRL, CLEAR)
    await ClockCycles(dut.clk, 3)
    st = await reconstruct(dut)
    if not (st & DONE and st & K_READY) or st & (ERR | RECON_FAIL):
        failures.append(f"reconstruction STATUS {st:#x}")
    if k_of(dut) != k:
        failures.append("reconstructed K differs from enrollment")
    if words_to_bytes(await read_words(dut, ID, 8)) != STATE["id"]:
        failures.append("ID after reconstruction differs")
    await scan(dut, secrets, failures, "after RECONSTRUCT")

    chal = np.random.default_rng(1).bytes(32)
    for i in range(8):
        await write(dut, CHAL + i, int.from_bytes(chal[4 * i:4 * i + 4], "big"))
    st = await command(dut, AUTH)
    resp = words_to_bytes(await read_words(dut, RESP, 8))
    if st & ERR or resp != hmac.new(k, chal, hashlib.sha256).digest():
        failures.append(f"AUTH: STATUS {st:#x}, RESP {resp.hex()}")
    secrets.add(256, int(dut.u_dut.u_crypto.inner_q.value))   # erased: 0, not added
    await scan(dut, secrets, failures, "after AUTH")

    # A reconstruction with a wrong KCV must fail and leave no K.
    bad = list(STATE["helper"])
    bad[17] ^= 1
    st = await reconstruct(dut, bad)
    if not st & RECON_FAIL or st & K_READY or int(dut.u_dut.u_crypto.k_q.value):
        failures.append(f"wrong KCV: STATUS {st:#x}, K left {bool(int(dut.u_dut.u_crypto.k_q.value))}")
    await scan(dut, secrets, failures, "after failed RECONSTRUCT")

    if char_build():
        await write(dut, RAW_PAIR, 5)
        await write(dut, RAW_CTRL, 1)
        for _ in range(200):
            if not await read(dut, RAW_CTRL) & 1:
                break
        secrets.counts.add(await read(dut, RAW_A))
        await scan(dut, secrets, failures, "CHAR_BUILD raw race", expect_raw=True)
    dut._log.info(f"scanned against {len(secrets.windows())} secret windows and "
                  f"{len(secrets.counts)} raw counter values ({'char' if char_build() else 'release'} build)")
    finish(dut, failures)


@cocotb.test()
async def test_tamper_erases_within_three_cycles(dut):
    """tamper_n at random cycles: all state erased <= 3 edges; TAMPERED sticky."""
    await ensure_enrolled(dut)
    rng = np.random.default_rng(7)
    failures = []
    phases = ["recon"] * 3 + ["auth"] * 3 + ["idle"] * 2
    for trial, phase in enumerate(phases):
        await reset(dut)
        await reconstruct(dut)
        if not int(dut.u_dut.u_crypto.k_valid.value):
            failures.append(f"trial {trial}: no K before tamper")
            continue
        for i in range(8):
            await write(dut, CHAL + i, int(rng.integers(1, 1 << 32)))
        if phase == "recon":
            for i, w in enumerate(STATE["helper"]):
                await write(dut, HELPER + i, w)
            await write(dut, CTRL, RECON)
            delay = int(rng.integers(1, 45_000))
        elif phase == "auth":
            await write(dut, CTRL, AUTH)
            delay = int(rng.integers(1, 2_900))
        else:
            delay = int(rng.integers(1, 50))
        await ClockCycles(dut.clk, delay)
        await FallingEdge(dut.clk)
        before = nonzero_state(dut)
        dut.tamper_n.value = 0
        for _ in range(3):
            await RisingEdge(dut.clk)
        await ReadOnly()
        left = nonzero_state(dut)
        if left:
            failures.append(f"trial {trial} ({phase}, cycle {delay}): not erased after "
                            f"3 edges: {left}")
        if not before:
            failures.append(f"trial {trial}: nothing to erase (test too weak)")
        await Timer(1, units="ns")
        dut.tamper_n.value = 1
        await ClockCycles(dut.clk, 20)
        st = await read(dut, STATUS)
        if not st & TAMPERED or st & K_READY:
            failures.append(f"trial {trial}: STATUS {st:#x} after tamper")
        # Commands are refused while TAMPERED.
        await write(dut, CTRL, RECON)
        await ClockCycles(dut.clk, 20)
        if int(dut.u_dut.state.value) != 0 or int(dut.u_dut.fe_busy.value):
            failures.append(f"trial {trial}: command accepted while TAMPERED")
        if not await read(dut, STATUS) & TAMPERED:
            failures.append(f"trial {trial}: TAMPERED not sticky")
    await reset(dut)
    if await read(dut, STATUS) & TAMPERED:
        failures.append("TAMPERED survives rst")
    finish(dut, failures)


@cocotb.test()
async def test_illegal_state_clears(dut):
    """An illegal FSM state erases everything (bus FSM) or the local secrets."""
    await ensure_enrolled(dut)
    failures = []

    # Bus FSM: state 15 is not a state; it must CLEAR everything.
    await reset(dut)
    await reconstruct(dut)
    assert int(dut.u_dut.u_crypto.k_valid.value)
    await FallingEdge(dut.clk)
    dut.u_dut.state.value = 15
    await ClockCycles(dut.clk, 4)
    await ReadOnly()
    left = nonzero_state(dut)
    if left:
        failures.append(f"bus FSM illegal state: not erased: {left}")
    await Timer(1, units="ns")
    st = await read(dut, STATUS)
    if not st & ERR or st & K_READY:
        failures.append(f"bus FSM illegal state: STATUS {st:#x}")

    # fuzzy_ext: illegal state during a reconstruction erases its buffers.
    await reset(dut)
    for i, w in enumerate(STATE["helper"]):
        await write(dut, HELPER + i, w)
    await write(dut, CTRL, RECON)
    await ClockCycles(dut.clk, 20_000)
    if not int(dut.u_dut.u_fe.key_q.value):
        failures.append("fuzzy_ext: no key bits collected before the deposit")
    await FallingEdge(dut.clk)
    dut.u_dut.u_fe.state.value = 15
    await ClockCycles(dut.clk, 4)
    await ReadOnly()
    # helper_mask is the public helper data copied in; the secrets must go.
    left = [n for n in nonzero_state(dut, only="fe.") if n != "fe.helper_mask"]
    if left:
        failures.append(f"fuzzy_ext illegal state: not erased: {left}")
    await Timer(1, units="ns")
    await wait_idle(dut)

    # sidik_crypto: illegal state erases K.
    await reset(dut)
    await reconstruct(dut)
    await FallingEdge(dut.clk)
    dut.u_dut.u_crypto.state.value = 15
    await ClockCycles(dut.clk, 3)
    await ReadOnly()
    left = nonzero_state(dut, only="crypto.")
    if left:
        failures.append(f"sidik_crypto illegal state: not erased: {left}")
    finish(dut, failures)


@cocotb.test()
async def test_clear_command(dut):
    await ensure_enrolled(dut)
    await reset(dut)
    await reconstruct(dut)
    await write(dut, CTRL, AUTH)
    await ClockCycles(dut.clk, 500)          # CLEAR in the middle of AUTH
    await write(dut, CTRL, CLEAR)
    await ClockCycles(dut.clk, 3)
    await ReadOnly()
    left = nonzero_state(dut)
    assert not left, f"CLEAR left {left}"
    await Timer(1, units="ns")
    assert await read(dut, STATUS) == 0
