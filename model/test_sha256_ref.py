# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

import unittest

from sha256_ref import BLOCK_BYTES, digest, pad_message, to_blocks


class PadMessageTest(unittest.TestCase):
    def test_block_count_at_boundaries(self):
        # 55 bytes is the longest message that still fits one block.
        for size, blocks in [(0, 1), (1, 1), (55, 1), (56, 2), (63, 2),
                             (64, 2), (119, 2), (120, 3)]:
            with self.subTest(size=size):
                self.assertEqual(len(to_blocks(b"a" * size)), blocks)

    def test_padding_layout(self):
        msg = b"abc"
        padded = pad_message(msg)
        self.assertEqual(len(padded) % BLOCK_BYTES, 0)
        self.assertEqual(padded[:3], msg)
        self.assertEqual(padded[3], 0x80)
        self.assertEqual(int.from_bytes(padded[-8:], "big"), 24)
        self.assertEqual(set(padded[4:-8]), {0})

    def test_digest_known_vector(self):
        # FIPS 180-2 Appendix B.1
        self.assertEqual(
            digest(b"abc").hex(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )


if __name__ == "__main__":
    unittest.main()
