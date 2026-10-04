// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Testbench for rtl/sidik_avmm.v: 50 MHz clock and the DUT with short races
// (LOG2N = 8) and 4 races per pair at enrollment (N_ENROLL = 4, release
// default 16), so that a full 512-pair enrollment simulates in seconds.
// The ring oscillators are the behavioural model of rtl/ro_cell.v (-DSIM).
module tb;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         rst = 1'b1;
    reg         tamper_n = 1'b1;
    reg  [5:0]  avs_address = 6'd0;
    reg         avs_write = 1'b0, avs_read = 1'b0;
    reg  [31:0] avs_writedata = 32'd0;
    wire [31:0] avs_readdata;

    sidik_avmm #(.LOG2N(8), .N_ENROLL(4), .MEAS_TIMEOUT(32'd100000)) u_dut (
        .clk(clk), .rst(rst), .tamper_n(tamper_n),
        .avs_address(avs_address), .avs_write(avs_write),
        .avs_writedata(avs_writedata), .avs_read(avs_read),
        .avs_readdata(avs_readdata));
endmodule

`default_nettype wire
