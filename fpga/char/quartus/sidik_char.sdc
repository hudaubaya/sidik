# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Timing constraints for the RO-PUF characterization build (DE10-Nano).
#
# Clock domains:
#   clk50                FPGA_CLK1_50: Avalon-MM, puf_meas FSM, synchronizers
#   altera_reserved_tck  JTAG (USB-Blaster II)
#   ro_a, ro_b           selected RO outputs, clocking only prescaler stage 0
#   ro_a_div, ro_b_div   prescaler outputs (f_RO / 2), clocking the counters
#
# The ring oscillators themselves are excluded from timing analysis: they
# are combinational loops with no timing requirement. Only the logic they
# clock is constrained, so the fitter checks that the first prescaler
# flip-flop and the counters can run at the assumed RO frequency.
#
# Assumes PRESCALE_LOG2 = 1 in rtl/puf_meas.v (one divide-by-2 stage).
# Wildcard names match the hierarchy of rtl/puf_meas.v and rtl/ro_array.v;
# after a compile, "Report Ignored Constraints" and "Report Unconstrained
# Paths" must both be empty.

# Assumed RO period. Not measured: replace with the period of the fastest
# RO measured on the board (measure_pairs.tcl mode=freq), minus a margin.
set RO_PERIOD_NS 2.5

# --- 50 MHz system clock -----------------------------------------------------
create_clock -name clk50 -period 20.000 [get_ports {FPGA_CLK1_50}]

# --- JTAG --------------------------------------------------------------------
create_clock -name altera_reserved_tck -period 40.000 [get_ports {altera_reserved_tck}]
set_input_delay  -clock altera_reserved_tck -clock_fall 3 [get_ports {altera_reserved_tdi}]
set_input_delay  -clock altera_reserved_tck -clock_fall 3 [get_ports {altera_reserved_tms}]
set_output_delay -clock altera_reserved_tck 3 [get_ports {altera_reserved_tdo}]

# --- RO clocks ---------------------------------------------------------------
# The selected RO drives the clock pin of prescaler stage 0 (g_div.pre[0],
# the only stage), whose output clocks the counter. "pre*" instead of
# "pre[0]" avoids bracket escaping in the name pattern.
foreach side {a b} {
    set pre0_clk [get_pins -compatibility_mode "*u_meas|u_cnt_${side}|g_div.pre*|clk"]
    create_clock -name ro_${side} -period $RO_PERIOD_NS $pre0_clk
    create_generated_clock -name ro_${side}_div -source $pre0_clk -divide_by 2 \
        [get_registers "*u_meas|u_cnt_${side}|g_div.pre*"]
}

# The ring oscillators: no timing analysis through any stage.
set_false_path -through [get_nets -compatibility_mode {*u_core|u_array|*}]

derive_clock_uncertainty

# --- Clock domain crossings --------------------------------------------------
# All crossings in rtl/puf_meas.v are handled in the design:
#   - halted_a/b -> clk50 and done (other RO) -> stop_sync: 2-flop synchronizers;
#   - count_a/b are captured in clk50 only after both counters halted;
#   - cnt_clr (asynchronous clear of the RO-domain flops) changes only while
#     the ROs are off, so it has no recovery/removal requirement against them.
set_clock_groups -asynchronous \
    -group {clk50} \
    -group {altera_reserved_tck} \
    -group {ro_a ro_a_div} \
    -group {ro_b ro_b_div}

# --- Board I/O ---------------------------------------------------------------
# KEY[0] is synchronized in sidik_char_top.v; LEDs are status only.
set_false_path -from [get_ports {KEY[*]}]
set_false_path -to   [get_ports {LED[*]}]
