# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Platform Designer component for the SIDIK RO-PUF core (rtl/ropuf/ropuf_avmm.v).
# Register map: rtl/ropuf/README.md. 32-bit Avalon-MM slave, word addresses,
# fixed read latency 1, no waitrequest.
#
# The RTL files are referenced in place from rtl/, so the bitstream always
# uses the same sources as the simulation tests. The CYCLONEV macro (set in
# sidik_char.qsf) selects the LCELL ring oscillators in rtl/ro_cell.v.

package require -exact qsys 16.1

set_module_property NAME sidik_ropuf
set_module_property VERSION 1.0
set_module_property DISPLAY_NAME "SIDIK RO-PUF (characterization)"
set_module_property DESCRIPTION "1024 ring oscillators in pairs, racing counters, Avalon-MM registers"
set_module_property GROUP SIDIK
set_module_property AUTHOR "Universitas Sriwijaya"
set_module_property EDITABLE false
set_module_property INTERNAL false
set_module_property INSTANTIATE_IN_SYSTEM_MODULE true

set rtl [file join .. .. .. rtl]

add_fileset QUARTUS_SYNTH QUARTUS_SYNTH "" ""
set_fileset_property QUARTUS_SYNTH TOP_LEVEL ropuf_avmm
add_fileset_file ropuf_avmm.v VERILOG PATH [file join $rtl ropuf ropuf_avmm.v] TOP_LEVEL_FILE
add_fileset_file ropuf_core.v VERILOG PATH [file join $rtl ropuf ropuf_core.v]
add_fileset_file ro_array.v   VERILOG PATH [file join $rtl ro_array.v]
add_fileset_file ro_cell.v    VERILOG PATH [file join $rtl ro_cell.v]
add_fileset_file puf_meas.v   VERILOG PATH [file join $rtl puf_meas.v]

add_parameter N_RO INTEGER 1024 "Number of ring oscillators (even)"
set_parameter_property N_RO HDL_PARAMETER true
add_parameter N_STAGES INTEGER 5 "Stages per ring oscillator (odd)"
set_parameter_property N_STAGES HDL_PARAMETER true
add_parameter LOG2N INTEGER 14 "Counter threshold 2^LOG2N RO cycles"
set_parameter_property LOG2N HDL_PARAMETER true

# Clock and reset (synchronous, active high in the RTL).
add_interface clock clock end
set_interface_property clock clockRate 0
add_interface_port clock clk clk Input 1

add_interface reset reset end
set_interface_property reset associatedClock clock
set_interface_property reset synchronousEdges DEASSERT
add_interface_port reset reset reset Input 1

# Avalon-MM slave.
add_interface avs avalon end
set_interface_property avs addressUnits WORDS
set_interface_property avs associatedClock clock
set_interface_property avs associatedReset reset
set_interface_property avs bitsPerSymbol 8
set_interface_property avs readLatency 1
set_interface_property avs readWaitTime 0
set_interface_property avs writeWaitTime 0
set_interface_property avs maximumPendingReadTransactions 0
add_interface_port avs avs_address   address   Input  3
add_interface_port avs avs_write     write     Input  1
add_interface_port avs avs_writedata writedata Input  32
add_interface_port avs avs_read      read      Input  1
add_interface_port avs avs_readdata  readdata  Output 32
