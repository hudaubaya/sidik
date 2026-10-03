# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/fuzzy_ext.v against model/ro_puf.py.

Every race either side sees comes from one oracle: per chip, per table
(an enrollment temperature or one reconstruction) and per pair, a stream of
races drawn with model/ro_puf.race() from a virtual chip (model.Chip). The
RTL reads the stream through tb.v; the model reads it through a patched
ro_puf.race(). Both consume each pair's stream in order, so the order in
which the RTL visits pairs does not matter, and the model's own
enroll_from_mean() / reconstruct() / SECDED / KCV logic is the reference.

The key check (KCV) is done by the testbench, as a consumer would: on
key_valid it compares the model's KCV of the presented key with the stored
one and answers key_good. Chips with "kcv off" answer key_good = 1, which
is the model with kcv_bits = 0.
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, First, ReadOnly, RisingEdge, Timer

import ro_puf as rp

N_PAIRS = 512
N_SLOT = 16
N_TABLE = 8      # tables in tb.v
N_KEY = 216
N_CHIPS = int(os.environ.get("FE_CHIPS", 200))
N_RECON = 4
SEED = 20261003


# --- oracle -----------------------------------------------------------------

class Oracle:
    """Race streams per (table, pair), shared by the RTL and the model."""

    def __init__(self, chip, params, temps, seed):
        self.data = np.zeros((len(temps), N_PAIRS, N_SLOT), dtype=np.int64)
        rng = np.random.default_rng(seed)
        for t, temp in enumerate(temps):
            fa, fb = chip.pair_freqs(temp)
            self.data[t] = rp.race(fa, fb, params, rng, shape=(N_SLOT, N_PAIRS)).T
        self.used = np.zeros((len(temps), N_PAIRS), dtype=np.int64)

    def next(self, table, pair):
        k = self.used[table, pair]
        if k >= N_SLOT:
            raise RuntimeError(f"oracle exhausted: table {table} pair {pair}")
        self.used[table, pair] = k + 1
        return self.data[table, pair, k]

    def write_hex(self, path):
        # Pad to the full oracle memory of tb.v so no table of an earlier
        # chip can be read.
        full = np.zeros((N_TABLE, N_PAIRS, N_SLOT), dtype=np.int64)
        full[:len(self.data)] = self.data
        flat = full.reshape(-1) & 0xFFFF
        Path(path).write_text("\n".join(f"{v:04x}" for v in flat) + "\n")


class FakeChip:
    """Stands in for model.Chip: 'frequencies' carry table * N_PAIRS + pair,
    so the patched race() knows which stream to read."""

    def __init__(self, oracle, params, enroll_tables):
        self.oracle, self.params = oracle, params
        self.enroll_tables = enroll_tables   # temperature -> table
        self.recon_table = None

    def pair_freqs(self, temp_c, pairs=None):
        if pairs is None:
            idx = self.enroll_tables[float(temp_c)] * N_PAIRS + np.arange(N_PAIRS)
            return idx.astype(float), np.zeros(N_PAIRS)
        idx = self.recon_table * N_PAIRS + np.asarray(pairs)
        return idx[None, :].astype(float), np.zeros((1, len(pairs)))


@contextmanager
def races_from(oracle):
    def race(fa, fb, params, rng, shape=()):
        shape = np.broadcast_shapes(np.shape(fa), np.shape(fb), shape)
        idx = np.broadcast_to(fa, shape).astype(np.int64)
        out = np.empty(shape, dtype=np.int64)
        for i in np.ndindex(shape):   # C order: each pair's races in order
            out[i] = oracle.next(*divmod(int(idx[i]), N_PAIRS))
        return out
    with mock.patch.object(rp, "race", race):
        yield


# --- chip configurations ----------------------------------------------------

@dataclass
class ChipCase:
    index: int
    sigma_process: float
    tau: int
    enroll_temps: tuple
    kcv_bits: int
    recon_temps: tuple


def chip_case(i, rng):
    tau = (16, 0, 32, 64)[i % 4]
    sigma = 0.01
    if i % 25 == 24:                 # too few pairs: enrollment must fail
        sigma, tau = 0.005, 128
    temps = (-40.0, 85.0) if i % 5 == 0 else (25.0,)
    kcv = 0 if i % 7 == 3 else 32    # some chips without key check
    recon = tuple(float(t) for t in rng.uniform(-40, 85, N_RECON))
    return ChipCase(i, sigma, tau, temps, kcv, recon)


def bits_of(value, n):
    return np.array([(value >> j) & 1 for j in range(n)], dtype=bool)


def int_of(bits):
    return sum(1 << j for j, b in enumerate(bits) if b)


# --- testbench helpers ----------------------------------------------------------

async def reset(dut):
    dut.rst.value = 1
    await ClockCycles(dut.clk, 3)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 2)


async def load_oracle(dut, oracle):
    oracle.write_hex(cocotb.plusargs["ORACLE"])
    dut.load.value = 1
    await RisingEdge(dut.clk)
    dut.load.value = 0
    await RisingEdge(dut.clk)


async def select_table(dut, table):
    dut.table_sel.value = table
    dut.clear_used.value = 1
    await RisingEdge(dut.clk)
    dut.clear_used.value = 0
    await RisingEdge(dut.clk)


async def pulse(dut, name):
    getattr(dut, name).value = 1
    await RisingEdge(dut.clk)
    getattr(dut, name).value = 0


async def answer_key(dut, good):
    dut.key_good.value = int(good)
    dut.key_ack.value = 1
    await RisingEdge(dut.clk)
    dut.key_ack.value = 0
    dut.key_good.value = 0


async def wait_key_or_done(dut):
    """-> 'key' or 'done' (whichever comes first), at a time when the
    testbench may drive inputs again (1 ns after the clock edge)."""
    while True:
        await ReadOnly()
        if int(dut.key_valid.value):
            ev = "key"
            break
        if int(dut.done.value):
            ev = "done"
            break
        await First(RisingEdge(dut.key_valid), RisingEdge(dut.done))
    await Timer(1, units="ns")
    return ev


def buffers(dut):
    fe = dut.u_fe
    return {name: int(getattr(fe, name).value)
            for name in ("key_q", "sign_q", "pass_q", "acc", "votes")}


def check_erased(dut, failures, what, allow=()):
    for name, v in buffers(dut).items():
        if name not in allow and v != 0:
            failures.append(f"{what}: {name} not erased ({v:#x})")
    if not int(dut.key_valid.value) and int(dut.key.value) != 0:
        failures.append(f"{what}: key port not 0 while key_valid is low")


def used_counts(dut):
    return np.array([int(dut.used[p].value) for p in range(N_PAIRS)])


# --- one chip -------------------------------------------------------------------

async def run_chip(dut, case, stats, failures):
    params = rp.PufParams(sigma_process=case.sigma_process)
    rng = np.random.default_rng([SEED, case.index])
    chip = rp.Chip(params, rng)
    temps = list(case.enroll_temps) + list(case.recon_temps)
    oracle_rtl = Oracle(chip, params, temps, [SEED, case.index, 1])
    oracle_model = Oracle(chip, params, temps, [SEED, case.index, 1])
    await load_oracle(dut, oracle_rtl)
    kp = rp.KeyGenParams(tau=float(case.tau), enroll_temps_c=case.enroll_temps,
                         kcv_bits=case.kcv_bits)
    fake = FakeChip(oracle_model, params, {t: i for i, t in enumerate(case.enroll_temps)})
    tag = f"chip {case.index} (tau {case.tau}, enroll {case.enroll_temps}, kcv {case.kcv_bits})"

    # ---- enrollment: model ----
    with races_from(oracle_model):
        enr = rp.enroll(fake, kp, None)

    # ---- enrollment: RTL, one phase per temperature ----
    n_phase = len(case.enroll_temps)
    rtl_key = None
    for ph in range(n_phase):
        await select_table(dut, ph)
        dut.tau.value = case.tau
        dut.enroll_first.value = int(ph == 0)
        dut.enroll_last.value = int(ph == n_phase - 1)
        await pulse(dut, "cmd_enroll")
        ev = await wait_key_or_done(dut)
        if ev == "key":
            rtl_key = bits_of(int(dut.key.value), N_KEY)
            # Response buffers are erased before the key is handed out.
            check_erased(dut, failures, f"{tag} at enrollment key", allow=("key_q",))
            await answer_key(dut, True)
            ev = await wait_key_or_done(dut)
        last = ph == n_phase - 1
        check_erased(dut, failures, f"{tag} after enrollment phase {ph}",
                     allow=() if last else ("sign_q", "pass_q"))
        if not last and int(dut.u_fe.sign_q.value) == 0 and int(dut.u_fe.pass_q.value) == 0:
            failures.append(f"{tag}: phase {ph} kept no response bits")
        if (used_counts(dut) != oracle_model.used[ph]).any():
            failures.append(f"{tag}: enrollment phase {ph} race counts differ from model")

    if int(dut.n_pass.value) != enr.n_passing:
        failures.append(f"{tag}: n_pass {int(dut.n_pass.value)} != model {enr.n_passing}")
    if bool(int(dut.fail.value)) != (not enr.ok):
        failures.append(f"{tag}: enrollment fail {int(dut.fail.value)} != model {not enr.ok}")
    if not enr.ok:
        stats["enroll_fail"] += 1
        if int(dut.helper_mask.value) != 0 or rtl_key is not None:
            failures.append(f"{tag}: failed enrollment left helper data or a key")
        return
    stats["enroll_ok"] += 1
    stats["enroll_two_temps"] += n_phase > 1
    mask = sum(1 << int(p) for p in enr.helper.pairs)
    syn = sum(int(s) << (8 * b) for b, s in enumerate(enr.helper.syndromes))
    if int(dut.helper_mask.value) != mask:
        failures.append(f"{tag}: helper mask differs from model")
    if int(dut.helper_syn.value) != syn:
        failures.append(f"{tag}: syndromes {int(dut.helper_syn.value):#x} != model {syn:#x}")
    if rtl_key is None or (rtl_key != enr.key_bits).any():
        failures.append(f"{tag}: enrollment key differs from model")

    # ---- reconstructions ----
    dut.helper_mask_in.value = mask
    dut.helper_syn_in.value = syn
    for r, temp in enumerate(case.recon_temps):
        table = n_phase + r
        fake.recon_table = table
        with races_from(oracle_model):
            rec = rp.reconstruct(fake, enr.helper, [temp], kp, None)
        await select_table(dut, table)
        await pulse(dut, "cmd_recon")
        accepted = None
        while await wait_key_or_done(dut) == "key":
            presented = bits_of(int(dut.key.value), N_KEY)
            check_erased(dut, failures, f"{tag} recon {r} at key", allow=("key_q",))
            good = bool(rp._kcv_matches(presented[None, :], enr.helper.kcv, kp.kcv_bits)[0])
            if good:
                accepted = presented
            await answer_key(dut, good)
        check_erased(dut, failures, f"{tag} recon {r} done")
        got = (bool(int(dut.fail.value)), int(dut.attempts.value),
               bool(int(dut.kcv_caught.value)))
        want = (bool(rec.failed[0]), int(rec.attempts[0]), bool(rec.kcv_caught[0]))
        if got != want:
            failures.append(f"{tag} recon {r} at {temp:.1f} C: (fail, attempts, kcv_caught) "
                            f"{got} != model {want}")
        if not want[0] and (accepted is None or (accepted != rec.key_bits[0]).any()):
            failures.append(f"{tag} recon {r}: key differs from model")
        if (used_counts(dut) != oracle_model.used[table]).any():
            failures.append(f"{tag} recon {r}: race counts differ from model")
        if int(dut.overrun.value):
            failures.append(f"{tag} recon {r}: oracle overrun")
        stats["recon"] += 1
        stats["recon_fail"] += want[0]
        stats["recon_remeasured"] += want[1] > 1
        stats["recon_remeasured_ok"] += want[1] > 1 and not want[0]
        stats["kcv_caught"] += want[2]
        wrong = not want[0] and (rec.key_bits[0] != enr.key_bits).any()
        stats["silent_wrong_key"] += bool(wrong)


def finish(dut, failures):
    for f in failures[:10]:
        dut._log.error(f)
    assert not failures, f"{len(failures)} mismatches"


# --- tests ------------------------------------------------------------------------

@cocotb.test()
async def test_matches_model_on_virtual_chips(dut):
    """Enrollment and reconstruction identical to the model on N_CHIPS chips."""
    await reset(dut)
    rng = np.random.default_rng(SEED)
    keys = ("enroll_ok", "enroll_fail", "enroll_two_temps", "recon", "recon_fail",
            "recon_remeasured", "recon_remeasured_ok", "kcv_caught", "silent_wrong_key")
    stats = dict.fromkeys(keys, 0)
    failures = []
    for i in range(N_CHIPS):
        await run_chip(dut, chip_case(i, rng), stats, failures)
        if failures:
            break
    dut._log.info(f"{N_CHIPS} chips: " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    finish(dut, failures)
    if N_CHIPS >= 200:
        # The run must exercise every path that is compared.
        for k in keys:
            assert stats[k] > 0, f"no case of {k}: {stats}"


@cocotb.test()
async def test_invalid_helper_fails_without_measuring(dut):
    await reset(dut)
    await select_table(dut, 0)
    dut.helper_mask_in.value = (1 << (N_KEY - 1)) - 1   # 215 pairs
    dut.helper_syn_in.value = 0
    await pulse(dut, "cmd_recon")
    assert await wait_key_or_done(dut) == "done"
    assert int(dut.fail.value) == 1
    assert int(dut.n_races.value) == 0
    failures = []
    check_erased(dut, failures, "invalid helper")
    finish(dut, failures)


@cocotb.test()
async def test_abort_erases_pending_enrollment(dut):
    """Response bits kept between enrollment phases are erased by cmd_abort."""
    await reset(dut)
    rng = np.random.default_rng([SEED, 999])
    params = rp.PufParams()
    chip = rp.Chip(params, rng)
    oracle = Oracle(chip, params, [-40.0], [SEED, 999, 1])
    await load_oracle(dut, oracle)
    await select_table(dut, 0)
    dut.tau.value = 16
    dut.enroll_first.value = 1
    dut.enroll_last.value = 0
    await pulse(dut, "cmd_enroll")
    assert await wait_key_or_done(dut) == "done"
    assert int(dut.u_fe.sign_q.value) != 0, "phase 1 kept no response bits"
    await pulse(dut, "cmd_abort")
    await RisingEdge(dut.clk)
    await ReadOnly()
    failures = []
    check_erased(dut, failures, "after abort")
    finish(dut, failures)
    assert int(dut.done.value) == 1 and int(dut.fail.value) == 1


@cocotb.test()
async def test_key_port_silent_while_busy(dut):
    """The key output stays 0 while key bits are being collected."""
    await reset(dut)
    rng = np.random.default_rng([SEED, 7])
    params = rp.PufParams()
    chip = rp.Chip(params, rng)
    oracle = Oracle(chip, params, [25.0, 25.0], [SEED, 7, 1])
    await load_oracle(dut, oracle)
    await select_table(dut, 0)
    dut.tau.value = 64
    dut.enroll_first.value = 1
    dut.enroll_last.value = 1
    await pulse(dut, "cmd_enroll")
    assert await wait_key_or_done(dut) == "key"
    mask = int(dut.helper_mask.value)
    syn = int(dut.helper_syn.value)
    await answer_key(dut, True)
    await wait_key_or_done(dut)
    await select_table(dut, 1)
    dut.helper_mask_in.value = mask
    dut.helper_syn_in.value = syn
    await pulse(dut, "cmd_recon")
    leaks = 0
    for _ in range(3000):            # well into the vote collection
        await RisingEdge(dut.clk)
        await ReadOnly()
        if not int(dut.key_valid.value):
            leaks += int(dut.key.value) != 0
    assert int(dut.u_fe.key_q.value) != 0, "no key bits collected yet"
    assert leaks == 0, f"key port non-zero on {leaks} cycles before key_valid"
