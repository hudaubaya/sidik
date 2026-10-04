# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/sidik_crypto.v against Python hashlib / hmac.

For CRYPTO_CASES random 216-bit keys and 32-byte challenges:
  K   = SHA-256(packbits(key bits) || b"SIDIK-K")      (checked inside the DUT)
  tag = HMAC-SHA256(K, challenge)
and, every 10th case, ID = HMAC(K, b"SIDIK-ID") and HMAC(K, b"SIDIK-CHK").
The references are hashlib / hmac directly and, for K / ID / KCV, also the
functions of model/ro_puf.py. Every operation's latency (start to done) must
be identical; tb.v counts violations of the Shaman protocol, which must stay
0; after every operation the Shaman core's buffers and the wrapper's
intermediate registers must be erased.
"""

import hashlib
import hmac
import os

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

import ro_puf as rp

N_CASES = int(os.environ.get("CRYPTO_CASES", 1000))
SEED = 20261004
OP_DERIVE, OP_HMAC, OP_ID, OP_KCV = range(4)
NAMES = {OP_DERIVE: "derive", OP_HMAC: "hmac", OP_ID: "id", OP_KCV: "kcv"}

# Shaman registers that hold message, schedule or hash state.
CORE_STATE = [
    "shapi.apibuf", "shapi.blockproc_bp_initdat", "shapi.resultbyteOut",
    *[f"shapi.blockproc.hbuf{i}" for i in range(8)],
    *[f"shapi.blockproc.bp_{c}" for c in "abcdefgh"],
    "shapi.blockproc.opT1Output", "shapi.blockproc.opT2Output",
    "shapi.blockproc.t1.wt.wt_buf", "shapi.blockproc.t1.wt.wt_out",
]


def bits_to_int(bits):
    """key_bits port value: bit j of the port = key bit j."""
    return sum(1 << j for j, b in enumerate(bits) if b)


def handle(root, path):
    for part in path.split("."):
        root = getattr(root, part)
    return root


async def reset(dut):
    dut.rst.value = 1
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def run_op(dut, op, key_bits=None, challenge=None):
    """Start one operation; return (latency in cycles, result bytes, error)."""
    if key_bits is not None:
        dut.key_bits.value = bits_to_int(key_bits)
    if challenge is not None:
        dut.challenge.value = int.from_bytes(challenge, "big")
    dut.op.value = op
    dut.start.value = 1
    await RisingEdge(dut.clk)           # start sampled here (cycle 0)
    t0 = cocotb.utils.get_sim_time(units="ns")
    dut.start.value = 0
    await RisingEdge(dut.done)
    await ReadOnly()
    t1 = cocotb.utils.get_sim_time(units="ns")
    latency = round((t1 - t0) / 20)
    res = int(dut.result.value).to_bytes(32, "big")
    err = int(dut.error.value)
    await Timer(1, units="ns")
    return latency, res, err


def erased(dut, failures, what):
    for path in CORE_STATE:
        if int(handle(dut.u_crypto.u_shaman, path).value) != 0:
            failures.append(f"{what}: Shaman {path} not erased")
    for name in ("inner_q", "dig"):
        if int(getattr(dut.u_crypto, name).value) != 0:
            failures.append(f"{what}: {name} not erased")


def finish(dut, failures):
    for f in failures[:10]:
        dut._log.error(f)
    assert not failures, f"{len(failures)} failures"


@cocotb.test()
async def test_random_keys_and_challenges(dut):
    """K, HMAC, ID, KCV equal hashlib/hmac; latency identical; protocol kept."""
    await reset(dut)
    rng = np.random.default_rng(SEED)
    failures, latencies = [], {op: set() for op in NAMES}
    for case in range(N_CASES):
        key_bits = rng.random(216) < 0.5
        challenge = rng.bytes(32)
        want_k = hashlib.sha256(np.packbits(key_bits).tobytes() + b"SIDIK-K").digest()
        if want_k != rp.derive_key(key_bits):
            failures.append(f"case {case}: hashlib and model disagree on K")

        lat, _, err = await run_op(dut, OP_DERIVE, key_bits=key_bits)
        latencies[OP_DERIVE].add(lat)
        got_k = int(dut.u_crypto.k_q.value).to_bytes(32, "big")
        if got_k != want_k or err or not int(dut.k_valid.value):
            failures.append(f"case {case}: K {got_k.hex()} != {want_k.hex()} (error {err})")
        erased(dut, failures, f"case {case} derive")

        ops = [(OP_HMAC, challenge, hmac.new(want_k, challenge, hashlib.sha256).digest())]
        if case % 10 == 0:
            ops.append((OP_ID, None, rp.device_id(want_k)))
            kcv_full = hmac.new(want_k, b"SIDIK-CHK", hashlib.sha256).digest()
            if kcv_full[:4] != rp.key_check_value(want_k, 32):
                failures.append(f"case {case}: KCV reference mismatch")
            ops.append((OP_KCV, None, kcv_full))
        for op, ch, want in ops:
            lat, got, err = await run_op(dut, op, challenge=ch)
            latencies[op].add(lat)
            if got != want or err:
                failures.append(f"case {case} {NAMES[op]}: {got.hex()} != {want.hex()} "
                                f"(error {err})")
            erased(dut, failures, f"case {case} {NAMES[op]}")
        if failures:
            break

    dut._log.info("latencies (cycles): " + ", ".join(
        f"{NAMES[op]} {sorted(v)}" for op, v in latencies.items()))
    for op, v in latencies.items():
        if len(v) != 1:
            failures.append(f"{NAMES[op]} latency not constant: {sorted(v)}")
    if latencies[OP_DERIVE] != {int(dut.u_crypto.LAT_DERIVE.value)}:
        failures.append(f"derive latency {latencies[OP_DERIVE]} != LAT_DERIVE")
    hmac_lat = latencies[OP_HMAC] | latencies[OP_ID] | latencies[OP_KCV]
    if hmac_lat != {int(dut.u_crypto.LAT_HMAC.value)}:
        failures.append(f"HMAC latencies {hmac_lat} != LAT_HMAC")
    if int(dut.proto_err.value):
        failures.append(f"{int(dut.proto_err.value)} Shaman protocol violations")
    expected_strobes = N_CASES * (64 + 4 * 64) + ((N_CASES + 9) // 10) * 2 * 4 * 64
    if int(dut.n_strobes.value) != expected_strobes:
        failures.append(f"{int(dut.n_strobes.value)} byte strobes, expected {expected_strobes}")
    finish(dut, failures)


@cocotb.test()
async def test_hmac_without_key_fails_on_time(dut):
    await reset(dut)
    dut.clear.value = 1                  # no K, whatever earlier tests left
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    strobes = int(dut.n_strobes.value)
    lat, res, err = await run_op(dut, OP_HMAC, challenge=bytes(32))
    assert err == 1 and res == bytes(32)
    assert lat == int(dut.u_crypto.LAT_HMAC.value), lat
    assert int(dut.n_strobes.value) == strobes, "core was used without a key"


@cocotb.test()
async def test_clear_erases_key(dut):
    await reset(dut)
    rng = np.random.default_rng([SEED, 1])
    await run_op(dut, OP_DERIVE, key_bits=rng.random(216) < 0.5)
    await run_op(dut, OP_ID)
    assert int(dut.u_crypto.k_q.value) != 0 and int(dut.result.value) != 0
    dut.clear.value = 1
    await RisingEdge(dut.clk)
    dut.clear.value = 0
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.u_crypto.k_q.value) == 0
    assert int(dut.result.value) == 0
    assert int(dut.k_valid.value) == 0
