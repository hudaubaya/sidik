// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Testbench of the combined system: sidik_window (two SIDIK instances, A at
// 0x000 and B at 0x100, address decoder of fpga/release) driven by one bus
// master; key0 (KEY0, active low) is the shared tamper input. Short races
// (LOG2N = 8) and N_ENROLL = 4 as in tb/sidik_avmm; behavioural ROs (-DSIM),
// A = ROs 0..1023, B = ROs 1024..2047 of the model: two different chips.
module tb;
    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg         rst = 1'b1;
    reg         key0 = 1'b1;
    reg  [31:0] address = 32'd0;
    reg         write = 1'b0, read = 1'b0;
    reg  [31:0] writedata = 32'd0;
    wire [31:0] readdata;
    wire        readdatavalid;

    sidik_window #(.LOG2N(8), .N_ENROLL(4), .MEAS_TIMEOUT(32'd100000)) u_win (
        .clk(clk), .rst(rst), .tamper_n(key0),
        .address(address), .write(write), .writedata(writedata),
        .read(read), .readdata(readdata), .readdatavalid(readdatavalid));
endmodule

`default_nettype wire
