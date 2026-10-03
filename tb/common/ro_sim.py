# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Predictions for the behavioural ROs of rtl/ro_cell.v (-DSIM).

Times are integer femtoseconds, so the predictions are exact.
"""

MASK = (1 << 64) - 1


def half_fs(seed, idx):
    """Half-period of RO `idx` for +RO_SEED=`seed` (mirror of ro_cell.v)."""
    x = (seed * 0x9E3779B97F4A7C15 + idx * 0xBF58476D1CE4E5B9) & MASK
    x ^= x >> 31
    x = (x * 0x94D049BB133111EB) & MASK
    x ^= x >> 29
    return 1_940_000 + x % 120_001


def _ro_edges_by(h, t):
    """RO rising edges up to time t (first at h, then every 2h)."""
    return 0 if t < h else (t - h) // (2 * h) + 1


def _div_edge_ro_index(m, p):
    """RO rising-edge index of the m-th divided-clock rising edge."""
    return m if p == 0 else (m - 1) * (1 << p) + (1 << (p - 1))


def _div_edges_by(h, t, p):
    n = _ro_edges_by(h, t)
    if p == 0:
        return n
    first = 1 << (p - 1)
    return 0 if n < first else (n - first) // (1 << p) + 1


def expected_counts(h_a, h_b, log2n=14, prescale_log2=1, sync=2):
    """(count_a, count_b) in RO cycles reported by rtl/puf_meas.v."""
    p = prescale_log2
    m_max = 1 << (log2n - p)
    if h_a == h_b:
        return m_max << p, m_max << p
    fast, slow = (h_a, h_b) if h_a < h_b else (h_b, h_a)
    e = _div_edge_ro_index(m_max, p)
    t_win = fast + (e - 1) * 2 * fast
    m_slow = min(_div_edges_by(slow, t_win, p) + sync, m_max)
    win, lose = m_max << p, m_slow << p
    return (win, lose) if h_a < h_b else (lose, win)


def ideal_delta(h_a, h_b, log2n=14):
    """2^log2n (1 - f_slow/f_fast), signed like count_a - count_b."""
    n = 1 << log2n
    return n * (1 - h_a / h_b) if h_a < h_b else -n * (1 - h_b / h_a)
