# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Creates the stand-alone Platform Designer system release_sys.qsys (no HPS),
# for bring-up over JTAG only:
#
#   clk_0 (50 MHz) -- jtag_master (JTAG to Avalon Master Bridge)
#                          | master
#                +---------+---------+
#                v                   v
#     sidik_a (sidik, 0x000)   sidik_b (sidik, 0x100)
#      larik A, ro_region_a     larik B, ro_region_b
#
# The HPS system (lightweight bridge + JTAG) is add_to_ghrd.tcl; both use
# the same instance offsets (sw/sidik_regs.py INSTANCE_OFFSET), so the JTAG
# master sees A at 0x000 and B at 0x100 in either system.
#
# Run from this directory (build.sh does this):
#   qsys-script --script=release_sys.tcl --search-path="$PWD,$"

package require -exact qsys 16.1

set sidik_a_offset 0x000
set sidik_b_offset 0x100

create_system release_sys
set_project_property DEVICE_FAMILY {Cyclone V}
set_project_property DEVICE {5CSEBA6U23I7}

add_instance clk_0 clock_source
set_instance_parameter_value clk_0 clockFrequency {50000000.0}
set_instance_parameter_value clk_0 clockFrequencyKnown {1}
set_instance_parameter_value clk_0 resetSynchronousEdges {DEASSERT}

add_instance jtag_master altera_jtag_avalon_master
add_connection clk_0.clk jtag_master.clk
add_connection clk_0.clk_reset jtag_master.clk_reset

foreach {inst offset} [list sidik_a $sidik_a_offset sidik_b $sidik_b_offset] {
    add_instance $inst sidik
    set_instance_parameter_value $inst LOG2N {14}
    set_instance_parameter_value $inst N_ENROLL {16}
    add_connection clk_0.clk $inst.clock
    add_connection clk_0.clk_reset $inst.reset
    add_connection jtag_master.master $inst.avs
    set_connection_parameter_value jtag_master.master/$inst.avs baseAddress $offset
    # top-level port ${inst}_tamper_tamper_n
    add_interface ${inst}_tamper conduit end
    set_interface_property ${inst}_tamper EXPORT_OF $inst.tamper
}

add_interface clk clock sink
set_interface_property clk EXPORT_OF clk_0.clk_in
add_interface reset reset sink
set_interface_property reset EXPORT_OF clk_0.clk_in_reset

save_system release_sys.qsys
