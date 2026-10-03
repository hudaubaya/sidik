# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Creates the Platform Designer system char_sys.qsys:
#
#   clk_0 (50 MHz) ── jtag_master (JTAG to Avalon Master Bridge)
#                          │ master
#                          ▼
#                     ropuf_0 (sidik_ropuf, base 0x0000, 8 registers)
#
# Run from this directory (build.sh does this):
#   qsys-script --script=char_sys.tcl --search-path="$PWD,$"
#   qsys-generate char_sys.qsys --synthesis=VERILOG --part=5CSEBA6U23I7
#
# The register base address 0x0 is assumed by fpga/char/syscon/measure_pairs.tcl.

package require -exact qsys 16.1

create_system char_sys
set_project_property DEVICE_FAMILY {Cyclone V}
set_project_property DEVICE {5CSEBA6U23I7}

add_instance clk_0 clock_source
set_instance_parameter_value clk_0 clockFrequency {50000000.0}
set_instance_parameter_value clk_0 clockFrequencyKnown {1}
set_instance_parameter_value clk_0 resetSynchronousEdges {DEASSERT}

add_instance jtag_master altera_jtag_avalon_master

add_instance ropuf_0 sidik_ropuf
set_instance_parameter_value ropuf_0 N_RO {1024}
set_instance_parameter_value ropuf_0 N_STAGES {5}
set_instance_parameter_value ropuf_0 LOG2N {14}

add_connection clk_0.clk jtag_master.clk
add_connection clk_0.clk_reset jtag_master.clk_reset
add_connection clk_0.clk ropuf_0.clock
add_connection clk_0.clk_reset ropuf_0.reset

add_connection jtag_master.master ropuf_0.avs
set_connection_parameter_value jtag_master.master/ropuf_0.avs baseAddress {0x0000}

add_interface clk clock sink
set_interface_property clk EXPORT_OF clk_0.clk_in
add_interface reset reset sink
set_interface_property reset EXPORT_OF clk_0.clk_in_reset

save_system char_sys.qsys
