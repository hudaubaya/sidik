# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""cocotb tests for rtl/secded72.v against model/secded.py."""

import itertools

import cocotb
import numpy as np
from cocotb.triggers import Timer

import secded

N = secded.N
N_RANDOM = 10_000


def to_bits(value):
    return np.array([(value >> j) & 1 for j in range(N)], dtype=bool)


def to_int(bits):
    return int(sum(1 << j for j, b in enumerate(bits) if b))


def model(word_bits, helper):
    """Expected (syndrome, corrected, single_error, uncorrectable)."""
    syn = int(secded.syndrome(word_bits))
    corrected, detected = secded.decode(word_bits, helper)
    single = bool(secded.DECODE_LUT[syn ^ helper] >= 0)
    return syn, to_int(corrected), single, bool(detected)


async def apply(dut, word, helper):
    dut.word.value = word
    dut.helper.value = helper
    await Timer(1, units="ns")
    return (int(dut.syndrome.value), int(dut.corrected.value),
            bool(int(dut.single_error.value)), bool(int(dut.uncorrectable.value)))


def check(failures, what, got, want):
    if got != want:
        failures.append(f"{what}: got {got}, want {want}")


def finish(dut, failures, total):
    for f in failures[:5]:
        dut._log.error(f)
    assert not failures, f"{len(failures)} of {total} cases wrong"


BASES = [0, (1 << N) - 1, 0x5A5A_C3C3_0F0F_9696_E1]


@cocotb.test()
async def test_no_error(dut):
    failures = []
    for base in BASES:
        helper = int(secded.syndrome(to_bits(base)))
        syn, corr, single, unc = await apply(dut, base, helper)
        check(failures, f"base {base:#x}", (syn, corr, single, unc),
              (helper, base, False, False))
    finish(dut, failures, len(BASES))


@cocotb.test()
async def test_all_single_errors_corrected(dut):
    failures = []
    for base in BASES:
        helper = int(secded.syndrome(to_bits(base)))
        for j in range(N):
            word = base ^ (1 << j)
            syn, corr, single, unc = await apply(dut, word, helper)
            check(failures, f"base {base:#x} bit {j}", (corr, single, unc),
                  (base, True, False))
            check(failures, f"base {base:#x} bit {j} syndrome", syn ^ helper, 0x80 | j)
    finish(dut, failures, len(BASES) * N)
    dut._log.info(f"{len(BASES) * N} single errors corrected")


@cocotb.test()
async def test_all_double_errors_detected(dut):
    base = BASES[2]
    helper = int(secded.syndrome(to_bits(base)))
    pairs = list(itertools.combinations(range(N), 2))
    assert len(pairs) == 2556
    failures = []
    for i, j in pairs:
        word = base ^ (1 << i) ^ (1 << j)
        _, corr, single, unc = await apply(dut, word, helper)
        check(failures, f"bits {i},{j}", (corr, single, unc), (word, False, True))
    finish(dut, failures, len(pairs))
    dut._log.info(f"{len(pairs)} double errors detected")


@cocotb.test()
async def test_random_vectors_match_model(dut):
    """10,000 vectors: half with a random helper, half with 0-4 bit errors."""
    rng = np.random.default_rng(20261003)
    failures = []
    for n in range(N_RANDOM):
        base = int.from_bytes(rng.bytes(9), "little") & ((1 << N) - 1)
        if n % 2:
            helper = int(rng.integers(0, 256))
            word = base
        else:
            helper = int(secded.syndrome(to_bits(base)))
            word = base
            for j in rng.choice(N, size=int(rng.integers(0, 5)), replace=False):
                word ^= 1 << int(j)
        got = await apply(dut, word, helper)
        check(failures, f"vector {n} word={word:#x} helper={helper:#x}",
              got, model(to_bits(word), helper))
    finish(dut, failures, N_RANDOM)
    dut._log.info(f"{N_RANDOM} random vectors identical to model/secded.py")
