# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

import hashlib
import hmac
import itertools
import unittest
from dataclasses import replace

import numpy as np

import ro_puf as rp
import secded

QUIET = rp.PufParams(sigma_jitter=0.0, sigma_tempco=0.0)


class RaceTest(unittest.TestCase):
    def test_delta_matches_frequency_ratio(self):
        rng = np.random.default_rng(1)
        n = QUIET.count_threshold
        fa, fb = 250e6, 247.5e6
        d = rp.race(fa, fb, QUIET, rng, shape=(1000,))
        expected = n * (1 - fb / fa)
        self.assertTrue(np.all(np.abs(d - expected) <= 2))

    def test_sign_and_never_zero(self):
        rng = np.random.default_rng(2)
        fa = 250e6 * (1 + 1e-3 * rng.standard_normal(10000))
        fb = 250e6 * (1 + 1e-3 * rng.standard_normal(10000))
        d = rp.race(fa, fb, rp.PufParams(), rng)
        self.assertFalse(np.any(d == 0))
        # winner always ends at the threshold, so |delta| < threshold
        self.assertTrue(np.all(np.abs(d) < QUIET.count_threshold))


class KeyGenTest(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(3)
        self.chip = rp.Chip(rp.PufParams(), self.rng)
        self.kp = rp.KeyGenParams()

    def test_enrollment_helper_data(self):
        e = rp.enroll(self.chip, self.kp, self.rng)
        self.assertTrue(e.ok)
        self.assertEqual(e.helper.pairs.size, 216)
        self.assertEqual(e.helper.syndromes.shape, (3,))
        self.assertTrue(np.all(np.abs(e.mean_delta[e.helper.pairs]) >= self.kp.tau))
        # first 216 passing pairs, in index order
        passing = np.flatnonzero(np.abs(e.mean_delta) >= self.kp.tau)
        np.testing.assert_array_equal(e.helper.pairs, passing[:216])

    def test_enrollment_fails_without_enough_pairs(self):
        e = rp.enroll(self.chip, replace(self.kp, tau=1e9), self.rng)
        self.assertFalse(e.ok)
        self.assertIsNone(e.key_bits)

    def test_noiseless_reconstruction_is_exact(self):
        chip = rp.Chip(QUIET, self.rng)
        e = rp.enroll(chip, self.kp, self.rng)
        r = rp.reconstruct(chip, e.helper, np.full(20, 25.0), self.kp, self.rng)
        self.assertFalse(r.failed.any())
        self.assertTrue((r.attempts == 1).all())
        self.assertTrue((r.key_bits == e.key_bits).all())

    def test_single_flipped_pair_is_corrected(self):
        chip = rp.Chip(QUIET, self.rng)
        e = rp.enroll(chip, self.kp, self.rng)
        # Swap the two ROs of one selected pair so its bit always flips.
        j = e.helper.pairs[100]
        chip.f_nom[[2 * j, 2 * j + 1]] = chip.f_nom[[2 * j + 1, 2 * j]]
        r = rp.reconstruct(chip, e.helper, np.full(5, 25.0), self.kp, self.rng)
        self.assertFalse(r.failed.any())
        self.assertTrue((r.key_bits == e.key_bits).all())

    def test_double_flip_in_a_block_is_detected(self):
        chip = rp.Chip(QUIET, self.rng)
        e = rp.enroll(chip, self.kp, self.rng)
        for j in e.helper.pairs[[0, 1]]:  # both in block 0
            chip.f_nom[[2 * j, 2 * j + 1]] = chip.f_nom[[2 * j + 1, 2 * j]]
        r = rp.reconstruct(chip, e.helper, np.full(5, 25.0), self.kp, self.rng)
        self.assertTrue(r.failed.all())
        self.assertTrue((r.attempts == 1 + self.kp.max_remeasure).all())

    def test_default_model_reconstructs_across_temperature(self):
        e = rp.enroll(self.chip, self.kp, self.rng)
        temps = self.rng.uniform(-40, 85, 200)
        r = rp.reconstruct(self.chip, e.helper, temps, self.kp, self.rng)
        self.assertFalse(r.failed.any())
        self.assertTrue((r.key_bits == e.key_bits).all())


def _miscorrecting_triple():
    """Three block positions whose combined syndrome equals another column."""
    cols = secded.COLUMNS.tolist()
    for i, j, k in itertools.combinations(range(secded.N), 3):
        if cols[i] ^ cols[j] ^ cols[k] in cols:
            return [i, j, k]
    raise AssertionError("no miscorrecting triple")


class KeyCheckTest(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(5)
        self.kp = rp.KeyGenParams()

    def _chip_with_three_errors(self, kp):
        chip = rp.Chip(QUIET, self.rng)
        e = rp.enroll(chip, kp, self.rng)
        for j in e.helper.pairs[_miscorrecting_triple()]:  # block 0
            chip.f_nom[[2 * j, 2 * j + 1]] = chip.f_nom[[2 * j + 1, 2 * j]]
        return chip, e

    def test_helper_carries_kcv(self):
        chip = rp.Chip(rp.PufParams(), self.rng)
        e = rp.enroll(chip, self.kp, self.rng)
        key = rp.derive_key(e.key_bits)
        self.assertEqual(len(e.helper.kcv), 4)
        self.assertEqual(e.helper.kcv,
                         hmac.new(key, b"SIDIK-CHK", hashlib.sha256).digest()[:4])

    def test_miscorrection_is_silent_without_kcv(self):
        kp = replace(self.kp, kcv_bits=0)
        chip, e = self._chip_with_three_errors(kp)
        r = rp.reconstruct(chip, e.helper, np.full(5, 25.0), kp, self.rng)
        self.assertFalse(r.failed.any())
        self.assertTrue((r.key_bits != e.key_bits).any(axis=1).all())

    def test_miscorrection_is_caught_with_kcv(self):
        chip, e = self._chip_with_three_errors(self.kp)
        r = rp.reconstruct(chip, e.helper, np.full(5, 25.0), self.kp, self.rng)
        self.assertTrue(r.failed.all())
        self.assertTrue(r.kcv_caught.all())
        self.assertTrue((r.attempts == 1 + self.kp.max_remeasure).all())

    def test_kcv_length_must_be_bytes(self):
        with self.assertRaises(ValueError):
            rp.key_check_value(b"k" * 32, 12)


class KeyDerivationTest(unittest.TestCase):
    def test_key_id_and_tag(self):
        bits = np.zeros(216, dtype=bool)
        bits[0] = True  # MSB of the first byte
        key = rp.derive_key(bits)
        msg = b"\x80" + b"\x00" * 26 + b"SIDIK-K"
        self.assertEqual(key, hashlib.sha256(msg).digest())
        self.assertEqual(rp.device_id(key),
                         hmac.new(key, b"SIDIK-ID", hashlib.sha256).digest())
        self.assertEqual(rp.auth_tag(key, b"nonce"),
                         hmac.new(key, b"nonce", hashlib.sha256).digest())
        self.assertEqual(len(np.packbits(bits)), 216 // 8)
        self.assertEqual(secded.N * 3, 216)


if __name__ == "__main__":
    unittest.main()
