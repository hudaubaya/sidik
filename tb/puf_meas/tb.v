// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Test top for rtl/ro_array.v + rtl/puf_meas.v (behavioural ROs, -DSIM).
// Exposes the per-RO enables and outputs so the test can check that only
// the selected pair runs, plus one RO with an explicit HALF_PERIOD_NS.
module tb #(
    parameter integer N_RO = 1024,
    parameter integer LOG2N = 14,
    parameter integer PRESCALE_LOG2 = 1,
    parameter integer PAIR_W = $clog2(N_RO / 2)
) (
    input  wire              clk,
    input  wire              rst,
    input  wire              start,
    input  wire [PAIR_W-1:0] pair,
    input  wire [31:0]       timeout,
    output wire              busy,
    output wire              done,
    output wire              timed_out,
    output wire              bit_out,
    output wire [LOG2N:0]    mag,
    output wire [LOG2N:0]    count_a,
    output wire [LOG2N:0]    count_b,
    output reg  [PAIR_W-1:0] sel,
    output wire              ro_run,
    output wire [N_RO-1:0]   ro_en,
    output wire [N_RO-1:0]   ro_out,
    input  wire              probe_en,
    output wire              probe_out
);

    wire ro_a, ro_b;

    always @(posedge clk) begin
        if (rst)
            sel <= {PAIR_W{1'b0}};
        else if (start && !busy)
            sel <= pair;
    end

    ro_array #(.N_RO(N_RO)) u_array (
        .en(ro_run), .pair(sel), .ro_a(ro_a), .ro_b(ro_b),
        .ro_en(ro_en), .ro_out(ro_out));

    puf_meas #(.LOG2N(LOG2N), .PRESCALE_LOG2(PRESCALE_LOG2)) u_meas (
        .clk(clk), .rst(rst), .start(start), .timeout(timeout),
        .ro_a(ro_a), .ro_b(ro_b), .ro_run(ro_run),
        .busy(busy), .done(done), .timed_out(timed_out),
        .bit_out(bit_out), .mag(mag), .count_a(count_a), .count_b(count_b));

    // HALF_PERIOD_NS path of ro_cell: 1.25 ns half-period (400 MHz).
    ro_cell #(.INDEX(9999), .HALF_PERIOD_NS(1.25)) u_probe (
        .en(probe_en), .out(probe_out));

endmodule

`default_nettype wire
