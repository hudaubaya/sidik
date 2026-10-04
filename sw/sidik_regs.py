# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Register map of rtl/sidik_avmm.v (release build), as byte offsets.

The slave has word addresses; a bus master (lightweight HPS bridge, JTAG to
Avalon master) sees byte address = base + 4 * word. sw/sidik_regs.h holds the
same values for the C verifier; test_verifier.py checks both against the RTL.
"""

MAGIC_VALUE = 0x53444B31        # "SDK1"

# word addresses
W_MAGIC, W_CTRL, W_STATUS, W_TAU = 0, 1, 2, 3
W_HELPER, W_SYN, W_KCV = 4, 20, 21
W_CHAL, W_RESP, W_ID = 22, 30, 38
N_HELPER_WORDS = 18             # mask (16), syndromes (1), KCV (1)
WINDOW_BYTES = 0x100            # 64 words per instance

# byte offsets
MAGIC, CTRL, STATUS, TAU = (4 * w for w in (W_MAGIC, W_CTRL, W_STATUS, W_TAU))
HELPER, CHAL, RESP, ID = (4 * w for w in (W_HELPER, W_CHAL, W_RESP, W_ID))

# CTRL bits
ENROLL, RECONSTRUCT, AUTH, CLEAR = 1, 2, 4, 8
# STATUS bits
BUSY, DONE, ERR, K_READY, RECON_FAIL, TAMPERED = (1 << i for i in range(6))

# Instance offsets inside the SIDIK window (fpga/release: sidik_a, sidik_b).
INSTANCE_OFFSET = {"A": 0x000, "B": 0x100}
