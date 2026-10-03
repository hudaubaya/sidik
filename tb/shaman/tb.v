// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// cocotb wrapper for the Shaman SHA-256 core; driven by test_shaman.py.
module tb ();

    initial begin
        $dumpfile("tb.vcd");
        $dumpvars(0, tb);
    end

    reg        clk;
    reg        rst_n;
    reg        ena = 1'b1;
    reg  [7:0] data_in;
    reg        parallel_loading;
    reg        result_next;
    reg        start;
    reg        clockin_data;

    wire [7:0] uo_out;
    wire [7:0] uio_out;
    wire [7:0] uio_oe;

    wire [7:0] uio_in = {clockin_data, start, 2'b00,
                         result_next, parallel_loading, 2'b00};

    wire [7:0] digest_byte  = uo_out;
    wire       result_ready = uio_out[0];
    wire       busy         = uio_out[4];

    tt_um_psychogenic_shaman dut (
        .ui_in   (data_in),
        .uo_out  (uo_out),
        .uio_in  (uio_in),
        .uio_out (uio_out),
        .uio_oe  (uio_oe),
        .ena     (ena),
        .clk     (clk),
        .rst_n   (rst_n)
    );

endmodule
