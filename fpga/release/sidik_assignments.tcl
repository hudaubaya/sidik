# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Quartus assignments for a project containing the SIDIK instances sidik_a
# and sidik_b (release_sys.tcl or add_to_ghrd.tcl). Applies them to the
# project's QSF:
#   quartus_sh -t sidik_assignments.tcl <project> [<revision>]
# build.sh runs it on sidik_release; for the GHRD run it on the GHRD project
# (fpga/release/README.md). Running it again replaces the same assignments.
#
# Ring oscillators (combinational loops), see fpga/char/quartus/sidik_char.qsf:
# Quartus warns once per loop (expect 2048 rings) and cuts them in timing
# analysis. What matters is that the rings stay intact: CYCLONEV selects
# the LCELL implementation in rtl/ro_cell.v, LCELL buffers must not be
# ignored and physical synthesis must not touch the stages.
#
# Two LogicLock regions, one per PUF array (larik A, larik B), each holding
# that instance's ro_array and puf_meas. They start auto-sized and
# floating so the first compile fits; after the first good compile lock
# them (LL_AUTO_SIZE OFF, LL_STATE LOCKED, copy LL_ORIGIN / LL_WIDTH /
# LL_HEIGHT from the fitter) so every later bitstream places A and B in the
# same areas. Reserved: no other logic inside. The regions must not overlap.

package require ::quartus::project

if {[llength $quartus(args)] < 1} {
    puts "usage: quartus_sh -t sidik_assignments.tcl <project> \[<revision>\]"
    exit 1
}
set project [lindex $quartus(args) 0]
if {[llength $quartus(args)] > 1} {
    project_open $project -revision [lindex $quartus(args) 1]
} else {
    project_open $project
}
set here [file dirname [file normalize [info script]]]

set_global_assignment -name VERILOG_MACRO "CYCLONEV=1"
set_global_assignment -name IGNORE_LCELL_BUFFERS OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_COMBO_LOGIC OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_DUPLICATION OFF
set_global_assignment -name PHYSICAL_SYNTHESIS_REGISTER_RETIMING OFF
set_global_assignment -name SDC_FILE [file join $here sidik_ro.sdc]

# Synchronizers: puf_meas (RO domains <-> clk) and tamper_n.
foreach target {{*u_meas|halted_a_sync[*]} {*u_meas|halted_b_sync[*]}
                {*u_meas|u_cnt_?|stop_sync[*]} {*sidik_avmm:sidik_?|tamper_s[*]}} {
    set_instance_assignment -name SYNCHRONIZER_IDENTIFICATION "FORCED IF ASYNCHRONOUS" -to $target
}

foreach {inst region} {sidik_a ro_region_a sidik_b ro_region_b} {
    set_global_assignment -name LL_ENABLED ON -section_id $region
    set_global_assignment -name LL_AUTO_SIZE ON -section_id $region
    set_global_assignment -name LL_STATE FLOATING -section_id $region
    set_global_assignment -name LL_RESERVED ON -section_id $region
    set_global_assignment -name LL_SOFT OFF -section_id $region
    foreach member {ro_array:u_array puf_meas:u_meas} {
        set_instance_assignment -name LL_MEMBER_OF $region \
            -to "*sidik_avmm:${inst}|ropuf_core:u_core|${member}" -section_id $region
    }
}

export_assignments
project_close
