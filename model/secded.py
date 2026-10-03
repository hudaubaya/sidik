# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Hsiao (72,64) SECDED code, used as a syndrome secure sketch.

The PUF never encodes data with this code. Enrollment publishes the 8-bit
syndrome H·w of each 72-bit response block w; reconstruction computes
H·w' xor helper = H·e and decodes the error pattern e from it:

  * syndrome 0                 -> no error
  * syndrome equal to column j -> single-bit error at j, corrected
  * anything else              -> uncorrectable error detected
                                  (every 2-bit error lands here)

Three or more errors can alias to a column and be miscorrected silently;
that is inherent to SECDED and is counted separately by the Monte Carlo.
"""

from itertools import combinations

import numpy as np

N = 72
K = 64
R = N - K

OK = -1
DETECTED = -2


def _columns():
    # Hsiao: all columns distinct with odd weight. 56 weight-3 + 8 weight-5
    # columns for data, 8 weight-1 columns for the check bits.
    w3 = [sum(1 << b for b in c) for c in combinations(range(R), 3)]
    w5 = [sum(1 << b for b in c) for c in combinations(range(R), 5)][:K - len(w3)]
    w1 = [1 << b for b in range(R)]
    return w3 + w5 + w1


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
