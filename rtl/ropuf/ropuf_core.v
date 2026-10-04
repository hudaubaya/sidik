// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// RO-PUF measurement core: rtl/ro_array.v (1024 ROs, pair select) plus
// rtl/puf_meas.v (racing counters, sign and magnitude). Latches `pair` on
// `start` and reports count_a, count_b and delta = count_a - count_b in RO
// cycles once the measurement is done. See puf_meas.v for the sequence and
// the clock-domain crossings.
module ropuf_core #(
    parameter integer N_RO = 1024,
    parameter integer N_STAGES = 5,
    parameter integer LOG2N = 14,
    parameter integer PRESCALE_LOG2 = 1,
    parameter integer RO_INDEX_BASE = 0,   // SIM only, see ro_array
    parameter integer PAIR_W = $clog2(N_RO / 2)
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 start,
    input  wire [PAIR_W-1:0]    pair,
    input  wire [31:0]          timeout,
    output wire                 busy,
    output wire                 done,       // last measurement finished
    output wire                 timed_out,  // last measurement timed out
    output wire [LOG2N:0]       count_a,
    output wire [LOG2N:0]       count_b,
    output wire signed [LOG2N+1:0] delta
);

    reg  [PAIR_W-1:0] sel;
    wire              ro_run, ro_a, ro_b, bit_out;
    wire [LOG2N:0]    mag;

    // The pair only changes while idle; the ROs are enabled later (RUN).
    always @(posedge clk) begin
        if (rst)
            sel <= {PAIR_W{1'b0}};
        else if (start && !busy)
            sel <= pair;
    end

    ro_array #(.N_RO(N_RO), .N_STAGES(N_STAGES), .INDEX_BASE(RO_INDEX_BASE)) u_array (
        .en(ro_run), .pair(sel), .ro_a(ro_a), .ro_b(ro_b),
        .ro_en(), .ro_out());

    puf_meas #(.LOG2N(LOG2N), .PRESCALE_LOG2(PRESCALE_LOG2)) u_meas (
        .clk(clk), .rst(rst), .start(start), .timeout(timeout),
        .ro_a(ro_a), .ro_b(ro_b), .ro_run(ro_run),
        .busy(busy), .done(done), .timed_out(timed_out),
        .bit_out(bit_out), .mag(mag), .count_a(count_a), .count_b(count_b));

    assign delta = bit_out ? $signed({1'b0, mag}) : -$signed({1'b0, mag});

endmodule

`default_nettype wire
