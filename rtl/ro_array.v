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
            // Scalar nets per RO: in simulation, reading or writing one bit of
            // a 1024-bit vector net with 1024 drivers costs O(N) per toggle.
            wire e = en && (pair == i / 2);
            wire o;
            ro_cell #(.N_STAGES(N_STAGES), .INDEX(i)) u_ro (
                .en  (e),
                .out (o)
            );
            assign ro_en[i]  = e;
            assign ro_out[i] = o;
        end
    endgenerate

`ifdef SIM
    // Simulation only: select through OR trees of scalar nets, so that an RO
    // toggle costs O(log N) instead of re-resolving the 1024-bit ro_out
    // vector (about 4x faster in Icarus). Same function as the mux below,
    // because every RO except the selected pair outputs 0.
    localparam integer NP = N_RO / 2;
    localparam integer LV = $clog2(NP);
    genvar lv, n;
    generate
        for (lv = 0; lv <= LV; lv = lv + 1) begin : g_lv
            for (n = 0; n < (1 << (LV - lv)); n = n + 1) begin : g_n
                wire a, b;
                if (lv == 0) begin : g_leaf
                    if (n < NP) begin : g_ro_pair
                        assign a = g_ro[2 * n].o;
                        assign b = g_ro[2 * n + 1].o;
                    end else begin : g_pad
                        assign a = 1'b0;
                        assign b = 1'b0;
                    end
                end else begin : g_node
                    assign a = g_lv[lv - 1].g_n[2 * n].a | g_lv[lv - 1].g_n[2 * n + 1].a;
                    assign b = g_lv[lv - 1].g_n[2 * n].b | g_lv[lv - 1].g_n[2 * n + 1].b;
                end
            end
        end
    endgenerate
    assign ro_a = g_lv[LV].g_n[0].a;
    assign ro_b = g_lv[LV].g_n[0].b;
`else
    assign ro_a = ro_out[{pair, 1'b0}];
    assign ro_b = ro_out[{pair, 1'b1}];
`endif

endmodule

`default_nettype wire
