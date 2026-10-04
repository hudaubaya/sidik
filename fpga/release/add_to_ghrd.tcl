# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Adds the two SIDIK instances to the Platform Designer system of the
# Terasic DE10-Nano GHRD (soc_system.qsys, from the DE10-Nano CD,
# Demonstrations/SoC_FPGA/DE10_NANO_SoC_GHRD):
#
#   hps_0.h2f_lw_axi_master (lightweight HPS-to-FPGA bridge, 0xFF200000)
#        +-- sidik_a.avs at window + 0x000      (larik A)
#        +-- sidik_b.avs at window + 0x100      (larik B)
#   sidik_jtag (JTAG to Avalon Master Bridge, added here)
#        +-- sidik_a.avs at 0x000
#        +-- sidik_b.avs at 0x100
#   sidik_a.tamper, sidik_b.tamper exported -> KEY[0] in the GHRD top
#
# window = 0x40000 is the default of sw/verifier.py and sw/sidik_verifier.c
# (--window / -w). Linux on the HPS reaches A at 0xFF240000, B at 0xFF240100.
#
# Run in the GHRD directory, with fpga/release on the search path:
#   qsys-script --system-file=soc_system.qsys --script=<repo>/fpga/release/add_to_ghrd.tcl \
#       --search-path="<repo>/fpga/release,$"
# The GHRD instance names below are those of the Terasic GHRD as we know
# it, not checked against a specific CD release: override them with
# --cmd="set lw_master ...; set clk_src ..." if `Platform Designer` reports
# a missing instance, and check the address map (Window 0x40000-0x401FF
# must not overlap another slave of the lightweight bridge).

package require -exact qsys 16.1

if {![info exists lw_master]} { set lw_master hps_0.h2f_lw_axi_master }
if {![info exists clk_src]}   { set clk_src clk_0.clk }
if {![info exists resets]}    { set resets {clk_0.clk_reset hps_0.h2f_reset} }
if {![info exists window]}    { set window 0x40000 }

set sidik_a_offset 0x000
set sidik_b_offset 0x100

add_instance sidik_jtag altera_jtag_avalon_master
add_connection $clk_src sidik_jtag.clk
foreach r $resets { add_connection $r sidik_jtag.clk_reset }

foreach {inst offset} [list sidik_a $sidik_a_offset sidik_b $sidik_b_offset] {
    add_instance $inst sidik
    set_instance_parameter_value $inst LOG2N {14}
    set_instance_parameter_value $inst N_ENROLL {16}
    add_connection $clk_src $inst.clock
    foreach r $resets { add_connection $r $inst.reset }

    add_connection $lw_master $inst.avs
    set_connection_parameter_value $lw_master/$inst.avs baseAddress \
        [format 0x%x [expr {$window + $offset}]]
    add_connection sidik_jtag.master $inst.avs
    set_connection_parameter_value sidik_jtag.master/$inst.avs baseAddress $offset

    # soc_system port ${inst}_tamper_tamper_n
    add_interface ${inst}_tamper conduit end
    set_interface_property ${inst}_tamper EXPORT_OF $inst.tamper
}

save_system
