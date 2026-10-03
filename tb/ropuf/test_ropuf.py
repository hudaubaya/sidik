# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/ropuf through its Avalon-MM registers.

The ROs are the behavioural model in ro_cell.v (-DSIM): fixed, jitter-free
frequencies given by half_fs() below, so every count is predictable.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, ReadOnly

ID, PARAMS, CTRL, PAIR, COUNT_A, COUNT_B, DELTA, TIMEOUT = range(8)
N_PAIRS = 512
LOG2N = 14
N = 1 << LOG2N
SYNC_STAGES = 2  # loser's stop synchronizer in race_counter.v


def half_fs(idx):
    """Mirror of half_ns() in rtl/ropuf/ro_cell.v, in integer femtoseconds.

    2.0 ns * (1 + 1e-5 * (h - 2000)) = 2_000_000 fs + 20 fs * (h - 2000).
    """
    p = (idx * 2654435761) & 0xFFFFFFFFFFFFFFFF
    h = ((p & 0xFFFFFFFF) >> 8) % 4001
    return 2_000_000 + 20 * (h - 2000)


def expected(pair):
    """(count_a, count_b) for the jitter-free behavioural ROs.

    Both ROs start together; RO k has its first rising edge at half_k and
    then one every 2*half_k. The winner stops at N; the loser counts its
    edges up to the winner's N-th edge plus SYNC_STAGES more.
    """
    ha, hb = half_fs(2 * pair), half_fs(2 * pair + 1)
    if ha == hb:
        return N, N

    def edges_by(t, h):
        return (t - h) // (2 * h) + 1

    if ha < hb:
        t = ha + (N - 1) * 2 * ha
        return N, min(edges_by(t, hb) + SYNC_STAGES, N)
    t = hb + (N - 1) * 2 * hb
    return min(edges_by(t, ha) + SYNC_STAGES, N), N


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())  # 50 MHz
    dut.avs_address.value = 0
    dut.avs_write.value = 0
    dut.avs_read.value = 0
    dut.avs_writedata.value = 0
    dut.reset.value = 1
    await ClockCycles(dut.clk, 5)
    dut.reset.value = 0
    await ClockCycles(dut.clk, 2)


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
    value = dut.avs_readdata.value
    await RisingEdge(dut.clk)
    return value


def signed32(v):
    v = int(v)
    return v - (1 << 32) if v & (1 << 31) else v


async def measure(dut, pair, max_polls=10000):
    await write(dut, PAIR, pair)
    await write(dut, CTRL, 1)
    for _ in range(max_polls):
        status = int(await read(dut, CTRL))
        if not status & 1:
            break
        await ClockCycles(dut.clk, 50)
    else:
        raise AssertionError("measurement did not finish")
    return (status, int(await read(dut, COUNT_A)), int(await read(dut, COUNT_B)),
            signed32(await read(dut, DELTA)))


@cocotb.test()
async def test_id_and_params(dut):
    await setup(dut)
    assert int(await read(dut, ID)) == 0x50554631
    params = int(await read(dut, PARAMS))
    assert params & 0xFFFF == N_PAIRS
    assert (params >> 16) & 0xFF == LOG2N
    assert (params >> 24) & 0xFF == 5
    assert int(await read(dut, TIMEOUT)) == 1 << 20


@cocotb.test()
async def test_pairs_match_behavioural_model(dut):
    await setup(dut)
    for pair in (0, 1, 2, 100, 255, 511):
        status, ca, cb, delta = await measure(dut, pair)
        ea, eb = expected(pair)
        dut._log.info(f"pair {pair:3d}: count_a={ca} count_b={cb} delta={delta:+d} "
                      f"(expected {ea}, {eb})")
        assert status == 0b010, f"status {status:#b}"
        assert max(ca, cb) == N
        assert delta == ca - cb
        assert (ca, cb) == (ea, eb)
        assert (delta > 0) == (half_fs(2 * pair) < half_fs(2 * pair + 1))


@cocotb.test()
async def test_repeatable_and_pair_switching(dut):
    await setup(dut)
    first = await measure(dut, 7)
    other = await measure(dut, 8)
    again = await measure(dut, 7)
    assert first == again
    assert other[1:] != first[1:]


@cocotb.test()
async def test_start_while_busy_is_ignored(dut):
    await setup(dut)
    await write(dut, PAIR, 3)
    await write(dut, CTRL, 1)
    await ClockCycles(dut.clk, 20)
    assert int(await read(dut, CTRL)) & 1
    await write(dut, PAIR, 4)       # takes effect for the next START only
    await write(dut, CTRL, 1)       # ignored
    while int(await read(dut, CTRL)) & 1:
        await ClockCycles(dut.clk, 50)
    ca, cb = int(await read(dut, COUNT_A)), int(await read(dut, COUNT_B))
    assert (ca, cb) == expected(3)


@cocotb.test()
async def test_timeout(dut):
    await setup(dut)
    await write(dut, TIMEOUT, 100)  # far shorter than 2^14 RO cycles
    status, ca, cb, _ = await measure(dut, 0)
    assert status == 0b110, f"status {status:#b}"
    assert max(ca, cb) < N
