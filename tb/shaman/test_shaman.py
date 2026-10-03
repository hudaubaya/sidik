# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for the Shaman SHA-256 core against model/sha256_ref.py."""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from sha256_ref import digest, to_blocks

TIMEOUT_CYCLES = 1000

# Sizes around the 55/56/64-byte padding boundaries.
BOUNDARY_SIZES = [0, 1, 55, 56, 63, 64, 65, 119, 120, 128]
MESSAGE = b"SIDIK: Shaman SHA-256 regression vector for boundary sizes. " * 3


async def wait_while(dut, cond, what):
    for _ in range(TIMEOUT_CYCLES):
        if not cond():
            return
        await ClockCycles(dut.clk, 1)
    raise AssertionError(f"timeout waiting for {what}")


async def reset(dut, parallel):
    cocotb.start_soon(Clock(dut.clk, 10, units="us").start())
    dut.data_in.value = 0
    dut.parallel_loading.value = int(parallel)
    dut.result_next.value = 0
    dut.start.value = 0
    dut.clockin_data.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


async def hash_message(dut, message):
    dut.start.value = 1
    await ClockCycles(dut.clk, 1)
    dut.start.value = 0
    await ClockCycles(dut.clk, 1)

    for block in to_blocks(message):
        for byte in block:
            await wait_while(dut, lambda: dut.busy.value, "busy to drop")
            dut.data_in.value = byte
            dut.clockin_data.value = 1
            await ClockCycles(dut.clk, 2)  # 2 cycles required in parallel mode
            dut.clockin_data.value = 0
            await ClockCycles(dut.clk, 1)
        await ClockCycles(dut.clk, 1)
        await wait_while(dut, lambda: dut.busy.value and not dut.result_ready.value,
                         "block processing")

    await wait_while(dut, lambda: not dut.result_ready.value, "result_ready")
    await ClockCycles(dut.clk, 2)

    out = bytearray()
    for _ in range(32):
        out.append(int(dut.digest_byte.value))
        dut.result_next.value = 1
        await ClockCycles(dut.clk, 2)
        dut.result_next.value = 0
        await ClockCycles(dut.clk, 1)
    return bytes(out)


async def check_boundaries(dut, parallel):
    await reset(dut, parallel)
    for size in BOUNDARY_SIZES:
        msg = MESSAGE[:size]
        got = await hash_message(dut, msg)
        want = digest(msg)
        dut._log.info(f"len={size:3d} digest={got.hex()}")
        assert got == want, f"len={size}: got {got.hex()}, want {want.hex()}"


@cocotb.test()
async def test_known_vector_abc(dut):
    await reset(dut, parallel=False)
    got = await hash_message(dut, b"abc")
    assert got.hex() == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


@cocotb.test()
async def test_boundaries_sync(dut):
    await check_boundaries(dut, parallel=False)


@cocotb.test()
async def test_boundaries_parallel(dut):
    await check_boundaries(dut, parallel=True)
