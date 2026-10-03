# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Extended Hamming (72,64) SECDED code, used as a syndrome secure sketch.

Same principle as the Hamming code of ECC_test1: the syndrome of a single
error is the position of the bit in error. Bit j of a 72-bit word (j = 0..71)
has parity-check column

    h_j = 0x80 | j

i.e. syndrome bits 6..0 are the binary position j (a Hamming (127,120) code
shortened to positions 0..71) and syndrome bit 7 is the overall parity of
the word. Position 0 is the overall-parity bit, whose column is 0x80 alone.

The PUF never encodes data with this code. Enrollment publishes the 8-bit
syndrome H·w of each 72-bit response block w; reconstruction computes
H·w' xor helper = H·e and decodes the error pattern e from it:

  * syndrome 0                          -> no error
  * bit 7 set, bits 6..0 = j <= 71      -> single-bit error at j, corrected
  * bit 7 set, bits 6..0 > 71           -> uncorrectable, detected
  * bit 7 clear, bits 6..0 != 0         -> uncorrectable, detected
                                           (every 2-bit error lands here)

Three or more errors can alias to a column and be miscorrected silently;
that is inherent to SECDED and is counted separately by the Monte Carlo.
rtl/secded72.v implements the same code and is checked against this file.
"""

import numpy as np

N = 72
K = 64
R = N - K

OK = -1
DETECTED = -2


def _columns():
    # Extended Hamming: syndrome bits 6..0 = position, bit 7 = overall parity.
    return [0x80 | j for j in range(N)]


COLUMNS = np.array(_columns(), dtype=np.int64)
H = ((COLUMNS[None, :] >> np.arange(R)[:, None]) & 1).astype(np.uint8)  # (8, 72)
_WEIGHTS = (1 << np.arange(R)).astype(np.int64)

# Syndrome value -> OK, DETECTED, or the bit position to flip.
DECODE_LUT = np.full(1 << R, DETECTED, dtype=np.int64)
DECODE_LUT[0] = OK
DECODE_LUT[COLUMNS] = np.arange(N)


def syndrome(words):
    """Syndromes of `words` (..., 72) bool/0-1 -> int array (...)."""
    words = np.asarray(words, dtype=np.uint8)
    bits = (words @ H.T) & 1  # (..., 8)
    return bits.astype(np.int64) @ _WEIGHTS


def decode(words, helper_syndromes):
    """Correct `words` (..., 72) against enrollment syndromes.

    Returns (corrected words, detected mask). Detected words are returned
    unmodified.
    """
    words = np.asarray(words, dtype=bool)
    s = syndrome(words) ^ np.asarray(helper_syndromes, dtype=np.int64)
    pos = DECODE_LUT[s]
    out = words.copy()
    flat = out.reshape(-1, N)
    flat_pos = pos.reshape(-1)
    rows = np.flatnonzero(flat_pos >= 0)
    flat[rows, flat_pos[rows]] ^= True
    return out, pos == DETECTED
