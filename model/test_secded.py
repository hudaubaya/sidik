# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

import itertools
import unittest

import numpy as np

import secded


class SecdedTest(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.word = rng.random(secded.N) < 0.5
        self.helper = secded.syndrome(self.word)

    def test_columns_are_extended_hamming(self):
        cols = secded.COLUMNS.tolist()
        self.assertEqual(len(cols), 72)
        self.assertEqual(len(set(cols)), 72)
        # bit 7 = overall parity, bits 6..0 = bit position
        self.assertEqual(cols, [0x80 | j for j in range(72)])

    def test_single_error_syndrome_is_position(self):
        for j in range(secded.N):
            e = np.zeros(secded.N, dtype=bool)
            e[j] = True
            self.assertEqual(int(secded.syndrome(e)), 0x80 | j)

    def test_no_error(self):
        out, det = secded.decode(self.word, self.helper)
        self.assertFalse(det)
        np.testing.assert_array_equal(out, self.word)

    def test_every_single_error_corrected(self):
        for j in range(secded.N):
            w = self.word.copy()
            w[j] ^= True
            out, det = secded.decode(w, self.helper)
            self.assertFalse(det, j)
            np.testing.assert_array_equal(out, self.word)

    def test_every_double_error_detected(self):
        pairs = list(itertools.combinations(range(secded.N), 2))
        words = np.tile(self.word, (len(pairs), 1))
        for row, (i, j) in enumerate(pairs):
            words[row, [i, j]] ^= True
        _, det = secded.decode(words, np.full(len(pairs), self.helper))
        self.assertTrue(det.all())

    def test_batch_shape(self):
        words = np.tile(self.word, (4, 3, 1))
        out, det = secded.decode(words, np.full((4, 3), self.helper))
        self.assertEqual(out.shape, (4, 3, 72))
        self.assertEqual(det.shape, (4, 3))


if __name__ == "__main__":
    unittest.main()
