# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/ro_array.v + rtl/puf_meas.v with random RO periods.

Every RO period is a pseudo-random function of +RO_SEED and the RO index
(tb/common/ro_sim.py mirrors rtl/ro_cell.v), so each count is predicted
exactly. A monitor checks on every clk edge that at most the selected pair
is enabled and that no other RO output is ever high.
"""

import random

import cocotb
import cocotb.utils
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

import ro_sim

LOG2N = 14
P = 1
N_RO = 1024
N_PAIRS = N_RO // 2
# |ideal delta| up to which a pair reads as a tie (delta = 0): the stop needs
# two divided cycles (2 * 2^P RO cycles) to cross the loser's synchronizer.
DEAD_ZONE = 2 << P


def seed():
    return int(cocotb.plusargs.get("RO_SEED", 1))


def predicted(pair):
    ha = ro_sim.half_fs(seed(), 2 * pair)
    hb = ro_sim.half_fs(seed(), 2 * pair + 1)
    return ha, hb, ro_sim.expected_counts(ha, hb, LOG2N, P)


class PairMonitor:
    """Samples ro_en / ro_out on every clk edge."""

    def __init__(self, dut):
        self.dut = dut
        self.violations = []
        self.active_samples = 0
        cocotb.start_soon(self.run())

    async def run(self):
        while True:
            await RisingEdge(self.dut.clk)
            sel = int(self.dut.sel.value)
            mask = 0b11 << (2 * sel)
            en = int(self.dut.ro_en.value)
            out = int(self.dut.ro_out.value)
            if en not in (0, mask):
                self.violations.append(f"ro_en={en:#x} with sel={sel}")
            if out & ~mask:
                self.violations.append(f"ro_out={out:#x} with sel={sel}")
            if en == mask:
                self.active_samples += 1


async def setup(dut):
    cocotb.start_soon(Clock(dut.clk, 20, units="ns").start())  # 50 MHz
    dut.start.value = 0
    dut.pair.value = 0
    dut.timeout.value = 1 << 20
    dut.probe_en.value = 0
    dut.rst.value = 1
    await ClockCycles(dut.clk, 5)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def measure(dut, pair):
    dut.pair.value = pair
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await RisingEdge(dut.clk)
    while int(dut.busy.value):
        await RisingEdge(dut.clk)
    return (int(dut.bit_out.value), int(dut.mag.value),
            int(dut.count_a.value), int(dut.count_b.value),
            int(dut.done.value), int(dut.timed_out.value))


async def check_pair(dut, pair, failures):
    bit, mag, ca, cb, done, tout = await measure(dut, pair)
    ha, hb, (ea, eb) = predicted(pair)
    ideal = ro_sim.ideal_delta(ha, hb, LOG2N)
    dut._log.info(f"pair {pair:3d}: bit={bit} |delta|={mag:4d} counts=({ca},{cb}) "
                  f"expected=({ea},{eb}) ideal={ideal:+.1f}")
    if (done, tout) != (1, 0):
        failures.append(f"pair {pair}: done={done} timed_out={tout}")
    if (ca, cb) != (ea, eb):
        failures.append(f"pair {pair}: counts ({ca},{cb}) != expected ({ea},{eb})")
    if ea == eb:
        # Dead zone: both counters reach 2^LOG2N before the stop crosses the
        # synchronizer; reported as delta = 0 with bit = 0.
        if (bit, mag) != (0, 0) or abs(ideal) > DEAD_ZONE:
            failures.append(f"pair {pair}: tie with bit={bit} mag={mag} "
                            f"ideal={ideal:+.2f}")
    elif bit != int(ha < hb):
        failures.append(f"pair {pair}: bit {bit} but RO {2 * pair} "
                        f"{'faster' if ha < hb else 'slower'}")
    if mag != abs(ca - cb) or mag % (1 << P):
        failures.append(f"pair {pair}: mag {mag} vs counts ({ca},{cb})")
    if abs(mag - abs(ideal)) > 4 << P:
        failures.append(f"pair {pair}: |delta| {mag} far from ideal {abs(ideal):.1f}")
    return bit


def finish(dut, failures):
    for f in failures[:5]:
        dut._log.error(f)
    assert not failures, f"{len(failures)} failures"


@cocotb.test()
async def test_random_pairs_sign_and_magnitude(dut):
    """24 random pairs in random order: exact counts, sign, magnitude.

    Pairs inside the dead zone must read as a tie; all others must have the
    sign of the true frequency difference.
    """
    await setup(dut)
    mon = PairMonitor(dut)
    rng = random.Random(seed())
    pairs = rng.sample(range(N_PAIRS), 24)
    failures = []
    bits = [await check_pair(dut, p, failures) for p in pairs]
    finish(dut, failures)
    assert 0 < sum(bits) < len(bits), "need both signs among the random pairs"
    assert not mon.violations, mon.violations[:3]
    assert mon.active_samples > 0


@cocotb.test()
async def test_pair_order(dut):
    """Ascending then descending sweeps; each result belongs to its pair."""
    await setup(dut)
    mon = PairMonitor(dut)
    pairs = [0, 1, 2, 3, 255, 256, 510, 511]
    failures = []
    for p in pairs + pairs[::-1]:
        await check_pair(dut, p, failures)
    finish(dut, failures)
    assert not mon.violations, mon.violations[:3]


@cocotb.test()
async def test_only_selected_pair_enabled(dut):
    """Enables and outputs of all other ROs stay 0 during a measurement."""
    await setup(dut)
    mon = PairMonitor(dut)
    for p in (5, 300):
        await measure(dut, p)
        assert int(dut.ro_en.value) == 0, "ROs left enabled after the measurement"
    assert not mon.violations, mon.violations[:3]
    # 2^14 cycles of a ~4 ns RO is ~33 us, i.e. >1000 clk samples per pair.
    assert mon.active_samples > 2000, mon.active_samples


@cocotb.test()
async def test_half_period_parameter(dut):
    """ro_cell with HALF_PERIOD_NS = 1.25 oscillates with a 2.5 ns period."""
    await setup(dut)
    dut.probe_en.value = 1
    await RisingEdge(dut.probe_out)
    t0 = cocotb.utils.get_sim_time(units="fs")
    for _ in range(100):
        await RisingEdge(dut.probe_out)
    period_fs = (cocotb.utils.get_sim_time(units="fs") - t0) / 100
    dut.probe_en.value = 0
    assert period_fs == 2_500_000, period_fs
    await Timer(10, units="ns")
    assert int(dut.probe_out.value) == 0


@cocotb.test()
async def test_timeout(dut):
    await setup(dut)
    dut.timeout.value = 100
    _, _, ca, cb, done, tout = await measure(dut, 7)
    assert (done, tout) == (1, 1)
    assert max(ca, cb) < 1 << LOG2N


