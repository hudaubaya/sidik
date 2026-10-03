# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""SHA-256 reference model for the SIDIK testbenches.

The Shaman core hashes pre-padded 512-bit blocks, so the host side (testbench,
firmware) must apply FIPS 180-4 padding. This module provides that padding
and the golden digest to compare the RTL against.
"""

import hashlib

BLOCK_BYTES = 64


def pad_message(message: bytes) -> bytes:
    """Apply FIPS 180-4 SHA-256 padding; result length is a multiple of 64."""
    message = bytes(message)
    bit_len = len(message) * 8
    padded = message + b"\x80"
    padded += b"\x00" * ((56 - len(padded)) % BLOCK_BYTES)
    padded += bit_len.to_bytes(8, "big")
    return padded


def to_blocks(message: bytes) -> list:
    """Pad `message` and split it into 64-byte blocks."""
    padded = pad_message(message)
    return [padded[i:i + BLOCK_BYTES] for i in range(0, len(padded), BLOCK_BYTES)]


def digest(message: bytes) -> bytes:
    """Golden SHA-256 digest."""
    return hashlib.sha256(bytes(message)).digest()
