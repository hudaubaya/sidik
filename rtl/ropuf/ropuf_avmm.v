// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Avalon-MM slave around ropuf_core (32-bit data, word addresses, fixed read
// latency 1, no waitrequest). Byte offsets:
//
//   0x00 ID       R   0x50554631 ("PUF1")
//   0x04 PARAMS   R   [15:0] pairs, [23:16] LOG2N, [31:24] RO stages
//   0x08 CTRL     W   bit0 START (ignored while busy)
//                 R   bit0 BUSY, bit1 DONE, bit2 TIMEOUT
//   0x0C PAIR     RW  pair index used by the next START
//   0x10 COUNT_A  R   counter of RO 2*pair (zero-extended)
//   0x14 COUNT_B  R   counter of RO 2*pair+1
//   0x18 DELTA    R   COUNT_A - COUNT_B, sign-extended to 32 bits
//   0x1C TIMEOUT  RW  RUN timeout in clk cycles
module ropuf_avmm #(
    parameter integer N_RO = 1024,
    parameter integer N_STAGES = 5,
    parameter integer LOG2N = 14,
    parameter [31:0]  TIMEOUT_RESET = 32'd1048576
) (
    input  wire        clk,
    input  wire        reset,
    input  wire [2:0]  avs_address,
    input  wire        avs_write,
    input  wire [31:0] avs_writedata,
    input  wire        avs_read,
    output reg  [31:0] avs_readdata
);

    localparam integer N_PAIRS = N_RO / 2;
    localparam integer PAIR_W = $clog2(N_PAIRS);
    localparam [31:0] ID = 32'h50554631;

    localparam [2:0] A_ID = 3'd0, A_PARAMS = 3'd1, A_CTRL = 3'd2, A_PAIR = 3'd3,
                     A_COUNT_A = 3'd4, A_COUNT_B = 3'd5, A_DELTA = 3'd6,
                     A_TIMEOUT = 3'd7;

    reg  [PAIR_W-1:0] pair;
    reg  [31:0]       timeout;
    wire              busy, done, timed_out;
    wire [LOG2N:0]    count_a, count_b;
    wire signed [LOG2N+1:0] delta;

    wire start = avs_write && avs_address == A_CTRL && avs_writedata[0];

    ropuf_core #(.N_RO(N_RO), .N_STAGES(N_STAGES), .LOG2N(LOG2N)) u_core (
        .clk(clk), .rst(reset), .start(start), .pair(pair), .timeout(timeout),
        .busy(busy), .done(done), .timed_out(timed_out),
        .count_a(count_a), .count_b(count_b), .delta(delta));

    always @(posedge clk) begin
        if (reset) begin
            pair    <= {PAIR_W{1'b0}};
            timeout <= TIMEOUT_RESET;
        end else if (avs_write) begin
            case (avs_address)
                A_PAIR:    pair    <= avs_writedata[PAIR_W-1:0];
                A_TIMEOUT: timeout <= avs_writedata;
                default: ;
            endcase
        end
    end

    always @(posedge clk) begin
        if (avs_read) begin
            case (avs_address)
                A_ID:      avs_readdata <= ID;
                A_PARAMS:  avs_readdata <= {N_STAGES[7:0], LOG2N[7:0], N_PAIRS[15:0]};
                A_CTRL:    avs_readdata <= {29'd0, timed_out, done, busy};
                A_PAIR:    avs_readdata <= {{(32-PAIR_W){1'b0}}, pair};
                A_COUNT_A: avs_readdata <= {{(31-LOG2N){1'b0}}, count_a};
                A_COUNT_B: avs_readdata <= {{(31-LOG2N){1'b0}}, count_b};
                A_DELTA:   avs_readdata <= {{(30-LOG2N){delta[LOG2N+1]}}, delta};
                default:   avs_readdata <= timeout;
            endcase
        end
    end

endmodule

`default_nettype wire
