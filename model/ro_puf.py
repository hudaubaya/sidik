# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Behavioural model of the SIDIK ring-oscillator PUF and its key generator.

Everything here is a model. The default parameters are assumptions chosen to
give plausible bit-error rates; they are not measurements and must be
replaced by values from fpga/char/ before any number derived from them is
quoted as a hardware result.

Chip model
    1024 ROs. RO i runs at f_i(T) = f0 (1 + sigma_process z_i)
    (1 + k_i (T - T_ref)), z_i ~ N(0, 1), k_i ~ N(tempco, sigma_tempco).
    Each measurement multiplies every RO frequency by (1 + sigma_jitter n),
    n ~ N(0, 1), drawn fresh per measurement (jitter averaged over the
    counting window).

Measurement
    Disjoint pairs (2i, 2i+1), i = 0..511. Both counters start at a random
    phase and race; when the first reaches 2^14 the other is sampled.
    delta = count(2i) - count(2i+1), never 0. Response bit = delta > 0.

Enrollment (at each temperature in enroll_temps_c, default T_ref only)
    mean of 16 deltas per pair per temperature; a pair passes when its sign
    is the same at every enrollment temperature and |mean| >= tau at each
    of them; take the first 216 passing pairs; split into 3 blocks of 72 bits; publish the extended
    Hamming (72,64) syndrome of each block (secded.py, = rtl/secded72.v). Helper data = mask of the 216 pairs
    + 3 x 8-bit syndromes + 32-bit key-check value.

Reconstruction
    3 measurements per pair, majority vote; per block: correct 1 bit, on a
    detected uncorrectable error re-measure that block (3 votes again).
    When every block decodes but the key-check value does not match (a
    SECDED miscorrection), re-measure all blocks. At most 3 re-measurement
    rounds; a block still uncorrectable or a key-check mismatch -> failure.

Key and identity
    K   = SHA-256(216 key bits, MSB first, || "SIDIK-K")
    KCV = first 32 bits of HMAC-SHA256(K, "SIDIK-CHK")
    ID  = HMAC-SHA256(K, "SIDIK-ID")
    tag = HMAC-SHA256(K, challenge)
"""

import hashlib
import hmac
from dataclasses import dataclass

import numpy as np

import secded

KEY_DOMAIN = b"SIDIK-K"
ID_DOMAIN = b"SIDIK-ID"
KCV_DOMAIN = b"SIDIK-CHK"


@dataclass(frozen=True)
class PufParams:
    n_ro: int = 1024
    f0_hz: float = 250e6
    sigma_process: float = 0.01   # relative std of RO frequency between ROs
    sigma_jitter: float = 3e-4    # relative std per measurement window
    tempco: float = -1e-3         # common relative tempco, 1/degC
    sigma_tempco: float = 2.5e-5  # per-RO tempco spread, 1/degC
    t_ref_c: float = 25.0
    count_threshold: int = 2 ** 14

    @property
    def n_pairs(self):
        return self.n_ro // 2


@dataclass(frozen=True)
class KeyGenParams:
    tau: float = 64.0             # mask threshold on |mean delta|, counts
    n_enroll: int = 16
    n_key_bits: int = 216
    n_votes: int = 3
    max_remeasure: int = 3
    kcv_bits: int = 32            # key-check value length; 0 disables it
    enroll_temps_c: tuple = (25.0,)  # enrollment temperatures, degC

    @property
    def n_blocks(self):
        return self.n_key_bits // secded.N


@dataclass(frozen=True)
class HelperData:
    pairs: np.ndarray       # indices of the 216 selected pairs (from the mask)
    syndromes: np.ndarray   # (n_blocks,) int
    kcv: bytes = b""        # key-check value, kcv_bits // 8 bytes


@dataclass(frozen=True)
class Enrollment:
    mean_delta: np.ndarray  # (n_temps, n_pairs) float, mean delta per temperature
    response: np.ndarray    # (n_pairs,) bool, sign at the first temperature
    n_passing: int          # pairs passing the mask
    helper: HelperData      # None if fewer than n_key_bits pairs pass
    key_bits: np.ndarray    # (n_key_bits,) bool, None if enrollment failed

    @property
    def ok(self):
        return self.helper is not None


@dataclass(frozen=True)
class Reconstruction:
    key_bits: np.ndarray    # (R, n_key_bits) bool
    failed: np.ndarray      # (R,) bool, uncorrectable after all re-measurements
    attempts: np.ndarray    # (R,) int, 1 + number of re-measurement rounds
    kcv_caught: np.ndarray  # (R,) bool, a key-check mismatch occurred
    first_votes: np.ndarray  # (R, n_votes, n_key_bits) bool, first attempt


class Chip:
    """One virtual chip: fixed process and tempco draws for every RO."""

    def __init__(self, params: PufParams, rng: np.random.Generator):
        self.params = params
        p = params
        self.f_nom = p.f0_hz * (1 + p.sigma_process * rng.standard_normal(p.n_ro))
        self.tempco = p.tempco + p.sigma_tempco * rng.standard_normal(p.n_ro)

    def freqs(self, temp_c):
        """RO frequencies at `temp_c` (scalar or (R,)) -> (n_ro,) or (R, n_ro)."""
        dt = np.asarray(temp_c, dtype=float)[..., None] - self.params.t_ref_c
        return self.f_nom * (1 + self.tempco * dt)

    def pair_freqs(self, temp_c, pairs=None):
        f = self.freqs(temp_c)
        a, b = f[..., 0::2], f[..., 1::2]
        if pairs is not None:
            a, b = a[..., pairs], b[..., pairs]
        return a, b


def race(fa, fb, params: PufParams, rng: np.random.Generator, shape=()):
    """One counter race per element of broadcast(fa, fb, shape).

    Returns delta = count_a - count_b (int64) when the faster counter
    reaches the threshold.
    """
    shape = np.broadcast_shapes(np.shape(fa), np.shape(fb), shape)
    sj = params.sigma_jitter
    fa = fa * (1 + sj * rng.standard_normal(shape))
    fb = fb * (1 + sj * rng.standard_normal(shape))
    pa = rng.random(shape)
    pb = rng.random(shape)
    n = params.count_threshold
    ta = (n - pa) / fa
    tb = (n - pb) / fb
    a_wins = ta <= tb
    t = np.minimum(ta, tb)
    ca = np.where(a_wins, n, np.floor(fa * t + pa))
    cb = np.where(a_wins, np.floor(fb * t + pb), n)
    return (ca - cb).astype(np.int64)


def measure_enrollment(chip: Chip, kp: KeyGenParams, rng):
    """Mean delta of kp.n_enroll races per pair at each enrollment temperature.

    Returns (len(kp.enroll_temps_c), n_pairs).
    """
    out = []
    for t in kp.enroll_temps_c:
        fa, fb = chip.pair_freqs(t)
        d = race(fa, fb, chip.params, rng, shape=(kp.n_enroll, fa.size))
        out.append(d.mean(axis=0))
    return np.array(out)


def enroll_from_mean(mean_delta, kp: KeyGenParams) -> Enrollment:
    mean_delta = np.atleast_2d(mean_delta)
    signs = mean_delta > 0
    response = signs[0]
    consistent = (signs == response).all(axis=0)
    strength = np.abs(mean_delta).min(axis=0)
    passing = np.flatnonzero(consistent & (strength >= kp.tau))
    if passing.size < kp.n_key_bits:
        return Enrollment(mean_delta, response, passing.size, None, None)
    pairs = passing[:kp.n_key_bits]
    key_bits = response[pairs]
    syn = secded.syndrome(key_bits.reshape(kp.n_blocks, secded.N))
    kcv = key_check_value(derive_key(key_bits), kp.kcv_bits)
    return Enrollment(mean_delta, response, passing.size,
                      HelperData(pairs, syn, kcv), key_bits)


def enroll(chip: Chip, kp: KeyGenParams, rng) -> Enrollment:
    return enroll_from_mean(measure_enrollment(chip, kp, rng), kp)


def _majority(fa, fb, n_votes, params, rng):
    """(M, n) pair freqs -> (votes (M, n_votes, n) bool, majority (M, n) bool)."""
    m, n = fa.shape
    votes = race(fa[:, None, :], fb[:, None, :], params, rng,
                 shape=(m, n_votes, n)) > 0
    return votes, votes.sum(axis=1) > n_votes // 2


def reconstruct(chip: Chip, helper: HelperData, temps_c, kp: KeyGenParams,
                rng) -> Reconstruction:
    """Reconstruct the key bits once per temperature in `temps_c` (R,)."""
    temps_c = np.atleast_1d(np.asarray(temps_c, dtype=float))
    n_rec, nb, n = temps_c.size, kp.n_blocks, secded.N
    fa, fb = chip.pair_freqs(temps_c, helper.pairs)  # (R, 216)

    votes, maj = _majority(fa, fb, kp.n_votes, chip.params, rng)
    words, detected = secded.decode(maj.reshape(n_rec, nb, n), helper.syndromes)
    attempts = np.ones(n_rec, dtype=np.int64)
    kcv_bad = np.zeros(n_rec, dtype=bool)

    def check(rows):
        # Key-check only reconstructions whose blocks all decoded.
        rows = rows[~detected[rows].any(axis=1)]
        kcv_bad[rows] = ~_kcv_matches(words[rows].reshape(rows.size, nb * n),
                                      helper.kcv, kp.kcv_bits)

    check(np.arange(n_rec))
    kcv_caught = kcv_bad.copy()

    cols = np.arange(n)
    for _ in range(kp.max_remeasure):
        redo = detected | kcv_bad[:, None]  # KCV mismatch: redo every block
        r_idx, b_idx = np.nonzero(redo)
        if r_idx.size == 0:
            break
        sel = b_idx[:, None] * n + cols  # (M, 72) pair columns of each block
        _, m = _majority(fa[r_idx[:, None], sel], fb[r_idx[:, None], sel],
                         kp.n_votes, chip.params, rng)
        w, d = secded.decode(m, helper.syndromes[b_idx])
        words[r_idx, b_idx] = w
        detected[r_idx, b_idx] = d
        rows = np.unique(r_idx)
        attempts[rows] += 1
        kcv_bad[rows] = False
        check(rows)
        kcv_caught |= kcv_bad

    return Reconstruction(key_bits=words.reshape(n_rec, nb * n),
                          failed=detected.any(axis=1) | kcv_bad,
                          attempts=attempts, kcv_caught=kcv_caught,
                          first_votes=votes)


def derive_key(key_bits) -> bytes:
    bits = np.asarray(key_bits, dtype=bool)
    return hashlib.sha256(np.packbits(bits).tobytes() + KEY_DOMAIN).digest()


def key_check_value(key: bytes, kcv_bits: int) -> bytes:
    if kcv_bits % 8:
        raise ValueError("kcv_bits must be a multiple of 8")
    return hmac.new(key, KCV_DOMAIN, hashlib.sha256).digest()[:kcv_bits // 8]


def _kcv_matches(key_bits_rows, kcv: bytes, kcv_bits: int):
    """(M, n_key_bits) candidate key bits -> (M,) bool."""
    if kcv_bits == 0:
        return np.ones(len(key_bits_rows), dtype=bool)
    packed = np.packbits(key_bits_rows, axis=1)
    return np.array([
        key_check_value(hashlib.sha256(row.tobytes() + KEY_DOMAIN).digest(),
                        kcv_bits) == kcv
        for row in packed], dtype=bool)


def device_id(key: bytes) -> bytes:
    return hmac.new(key, ID_DOMAIN, hashlib.sha256).digest()


def auth_tag(key: bytes, challenge: bytes) -> bytes:
    return hmac.new(key, challenge, hashlib.sha256).digest()
