// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// N_RO ring oscillators in disjoint pairs (2i, 2i+1). Only the two ROs of
// `pair` are enabled, and only while `en` is high; every other RO is held
// off. ro_a / ro_b are the outputs of RO 2*pair and RO 2*pair+1.
//
// `pair` must be stable whenever `en` is high (puf_meas only raises its
// RO enable after the pair has been latched).
module ro_array #(
    parameter integer N_RO = 1024,
    parameter integer N_STAGES = 5,
    parameter integer PAIR_W = $clog2(N_RO / 2)
) (
    input  wire              en,
    input  wire [PAIR_W-1:0] pair,
    output wire              ro_a,
    output wire              ro_b,
    output wire [N_RO-1:0]   ro_en,   // per-RO enables (for test/debug)
    output wire [N_RO-1:0]   ro_out   // per-RO outputs (for test/debug)
);

    genvar i;
    generate
        for (i = 0; i < N_RO; i = i + 1) begin : g_ro
            assign ro_en[i] = en && (pair == i / 2);
            ro_cell #(.N_STAGES(N_STAGES), .INDEX(i)) u_ro (
                .en  (ro_en[i]),
                .out (ro_out[i])
            );
        end
    endgenerate

    assign ro_a = ro_out[{pair, 1'b0}];
    assign ro_b = ro_out[{pair, 1'b1}];

endmodule

`default_nettype wire
