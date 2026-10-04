# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Timing constraints of the two SIDIK instances (sidik_a, sidik_b): the RO
# clocks and the asynchronous inputs. Added to the project by
# sidik_assignments.tcl, next to the project's own SDC (system clock, JTAG,
# board I/O: sidik_release_top.sdc, or the GHRD's).
#
# Per instance and per race counter (a, b):
#   <inst>_ro_<side>      selected RO output, clocking prescaler stage 0
#   <inst>_ro_<side>_div  prescaler output (f_RO / 2), clocking the counter
# Assumes PRESCALE_LOG2 = 1 (one divide-by-2 stage), as fpga/char.
# The ring oscillators themselves are excluded from timing analysis.
# After a compile "Report Ignored Constraints" must be empty.

# Assumed RO period, not measured: use the fastest RO measured on the board
# (fpga/char, measure_pairs.tcl mode=freq) minus a margin.
set RO_PERIOD_NS 2.5

foreach inst {sidik_a sidik_b} {
    foreach side {a b} {
        set path "*${inst}|u_core|u_meas|u_cnt_${side}|g_div.pre*"
        set pre0_clk [get_pins -compatibility_mode "${path}|clk"]
        create_clock -name ${inst}_ro_${side} -period $RO_PERIOD_NS $pre0_clk
        create_generated_clock -name ${inst}_ro_${side}_div -source $pre0_clk -divide_by 2 \
            [get_registers $path]
        # A single group: asynchronous to every other clock of the design.
        set_clock_groups -asynchronous -group [get_clocks "${inst}_ro_${side}*"]
    }
}

# The ring oscillators: no timing analysis through any stage.
set_false_path -through [get_nets -compatibility_mode {*sidik_?|u_core|u_array|*}]

# tamper_n (KEY0) is asynchronous; the first synchronizer flop is the
# endpoint. Its metastability is handled by the 2-flop synchronizer.
set_false_path -to [get_registers {*sidik_?|tamper_s[0]}]
