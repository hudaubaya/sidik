// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none

// Compile-check stand-in for the Platform Designer system release_sys
// (fpga/release/release_sys.tcl): same exported ports, the two SIDIK
// instances behind the decoder model tb/sidik_system/sidik_window.v with an
// idle bus (the real system adds the JTAG to Avalon master). Lets the top
// level and the component's RTL file list compile without Quartus.
module release_sys (
    input wire clk_clk,
    input wire reset_reset_n,
    input wire sidik_a_tamper_tamper_n,
    input wire sidik_b_tamper_tamper_n
);
    wire [31:0] readdata;
    wire        readdatavalid;

    // One tamper input per instance in release_sys; the model shares one.
    sidik_window u_win (
        .clk(clk_clk), .rst(~reset_reset_n),
        .tamper_n(sidik_a_tamper_tamper_n & sidik_b_tamper_tamper_n),
        .address(32'd0), .write(1'b0), .writedata(32'd0), .read(1'b0),
        .readdata(readdata), .readdatavalid(readdatavalid));
endmodule

`default_nettype wire
