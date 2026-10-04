# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Timing constraints of the stand-alone release top level (sidik_release_top.v).
# The SIDIK instances' RO clocks are in sidik_ro.sdc.

create_clock -name clk50 -period 20.000 [get_ports {FPGA_CLK1_50}]

create_clock -name altera_reserved_tck -period 40.000 [get_ports {altera_reserved_tck}]
set_input_delay  -clock altera_reserved_tck -clock_fall 3 [get_ports {altera_reserved_tdi}]
set_input_delay  -clock altera_reserved_tck -clock_fall 3 [get_ports {altera_reserved_tms}]
set_output_delay -clock altera_reserved_tck 3 [get_ports {altera_reserved_tdo}]
set_clock_groups -asynchronous -group {altera_reserved_tck}

derive_clock_uncertainty

# KEY[0] (tamper) and KEY[1] (reset) are synchronized in the design.
set_false_path -from [get_ports {KEY[*]}]
set_false_path -to   [get_ports {LED[*]}]
