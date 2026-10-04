/* SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Register map of rtl/sidik_avmm.v (release build) as byte offsets; same
 * values as sw/sidik_regs.py (sw/test_verifier.py checks both against the RTL).
 */
#ifndef SIDIK_REGS_H
#define SIDIK_REGS_H

#define SIDIK_MAGIC_VALUE    0x53444B31u   /* "SDK1" */

/* byte offsets inside one instance (word address * 4) */
#define SIDIK_MAGIC          0x00u
#define SIDIK_CTRL           0x04u
#define SIDIK_STATUS         0x08u
#define SIDIK_TAU            0x0Cu
#define SIDIK_HELPER         0x10u   /* 18 words: mask (16), syndromes, KCV */
#define SIDIK_CHAL           0x58u   /* 8 words, big endian */
#define SIDIK_RESP           0x78u
#define SIDIK_ID             0x98u
#define SIDIK_N_HELPER_WORDS 18
#define SIDIK_WINDOW_BYTES   0x100u

/* CTRL bits */
#define SIDIK_ENROLL         0x01u
#define SIDIK_RECONSTRUCT    0x02u
#define SIDIK_AUTH           0x04u
#define SIDIK_CLEAR          0x08u

/* STATUS bits */
#define SIDIK_BUSY           0x01u
#define SIDIK_DONE           0x02u
#define SIDIK_ERR            0x04u
#define SIDIK_K_READY        0x08u
#define SIDIK_RECON_FAIL     0x10u
#define SIDIK_TAMPERED       0x20u

/* instance offsets inside the SIDIK window (fpga/release) */
#define SIDIK_INSTANCE_A     0x000u
#define SIDIK_INSTANCE_B     0x100u

#endif
