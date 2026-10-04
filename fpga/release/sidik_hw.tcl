# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Platform Designer component for one SIDIK key generator, release build
# (rtl/sidik_avmm.v: RO-PUF, fuzzy extractor, Shaman SHA-256 / HMAC).
# Register map: rtl/sidik_avmm.v header, sw/sidik_regs.py, sw/sidik_regs.h.
# 32-bit Avalon-MM slave, 64 words (0x100 bytes), fixed read latency 1, no
# waitrequest. Conduit `tamper` (tamper_n, active low, asynchronous).
#
# The release build has no read path to K, key bits or raw counts.
# CHAR_BUILD is never defined here (fpga/char is the characterization build).
#
# RTL files are referenced in place from rtl/; the CYCLONEV macro
# (sidik_assignments.tcl) selects the LCELL ring oscillators. The component
# is GPL-3.0 because it contains the Shaman core (docs/baselines.md).

package require -exact qsys 16.1

set_module_property NAME sidik
set_module_property VERSION 1.0
set_module_property DISPLAY_NAME "SIDIK PUF key generator (release)"
set_module_property DESCRIPTION "RO-PUF + fuzzy extractor + HMAC-SHA256, Avalon-MM, tamper input"
set_module_property GROUP SIDIK
set_module_property AUTHOR "Universitas Sriwijaya"
set_module_property EDITABLE false
set_module_property INTERNAL false
set_module_property INSTANTIATE_IN_SYSTEM_MODULE true

set rtl [file join .. .. rtl]

add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL sidik_avmm
add_fileset_file sidik_avmm.v   VERILOG PATH [file join $rtl sidik_avmm.v] TOP_LEVEL_FILE
add_fileset_file ropuf_core.v   VERILOG PATH [file join $rtl ropuf ropuf_core.v]
add_fileset_file ro_array.v     VERILOG PATH [file join $rtl ro_array.v]
add_fileset_file ro_cell.v      VERILOG PATH [file join $rtl ro_cell.v]
add_fileset_file puf_meas.v     VERILOG PATH [file join $rtl puf_meas.v]
add_fileset_file secded72.v     VERILOG PATH [file join $rtl secded72.v]
add_fileset_file fuzzy_ext.v    VERILOG PATH [file join $rtl fuzzy_ext.v]
add_fileset_file tt_um_psychogenic_shaman.v VERILOG PATH [file join $rtl third_party shaman tt_um_psychogenic_shaman.v]
add_fileset_file sidik_crypto.v VERILOG PATH [file join $rtl sidik_crypto.v]

# N_RO is fixed at 1024 by the register map (16 mask words); PRESCALE_LOG2
# at 1 by the SDC. Only the race length and the enrollment averaging are
# parameters.
add_parameter LOG2N INTEGER 14 "Counter threshold 2^LOG2N RO cycles"
set_parameter_property LOG2N ALLOWED_RANGES 8:14
set_parameter_property LOG2N HDL_PARAMETER true
add_parameter N_ENROLL INTEGER 16 "Races averaged per pair at enrollment"
set_parameter_property N_ENROLL ALLOWED_RANGES {4 8 16 32}
set_parameter_property N_ENROLL HDL_PARAMETER true

# Clock and reset (synchronous, active high in the RTL).
add_interface clock clock end
set_interface_property clock clockRate 0
add_interface_port clock clk clk Input 1

add_interface reset reset end
set_interface_property reset associatedClock clock
set_interface_property reset synchronousEdges DEASSERT
add_interface_port reset rst reset Input 1

# Avalon-MM slave, 64 words.
add_interface avs avalon end
set_interface_property avs addressUnits WORDS
set_interface_property avs associatedClock clock
set_interface_property avs associatedReset reset
set_interface_property avs bitsPerSymbol 8
set_interface_property avs readLatency 1
set_interface_property avs readWaitTime 0
set_interface_property avs writeWaitTime 0
set_interface_property avs maximumPendingReadTransactions 0
add_interface_port avs avs_address   address   Input  6
add_interface_port avs avs_write     write     Input  1
add_interface_port avs avs_writedata writedata Input  32
add_interface_port avs avs_read      read      Input  1
add_interface_port avs avs_readdata  readdata  Output 32

# Tamper input (KEY0 on the DE10-Nano). Asynchronous: synchronized inside.
add_interface tamper conduit end
add_interface_port tamper tamper_n tamper_n Input 1
