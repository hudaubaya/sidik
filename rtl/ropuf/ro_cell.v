// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
// 1 fs precision: the behavioural half-periods are exact multiples of 20 fs,
// so no rounding accumulates over the 2^15 toggles of a measurement.
`timescale 1ns/1fs

// One ring oscillator: a NAND (enable) followed by N_STAGES-1 inverters, so
// N_STAGES must be odd. `out` is 0 while `en` is 0.
//
// Synthesis: every stage is a separate ro_stage instance with keep /
// keep_hierarchy / dont_touch, and every stage net carries keep attributes,
// so the tool cannot collapse the inverter chain (checked with yosys; for
// Vivado also set ALLOW_COMBINATORIAL_LOOPS on the nets). Placement
// constraints for the ROs are not part of this file.
//
// Simulation (`SIM` defined): a behavioural oscillator whose half-period is
// a deterministic function of INDEX (+-2 % around HALF_NS_NOMINAL), so a
// testbench can predict every frequency. This models no jitter.
module ro_cell #(
    parameter integer N_STAGES = 5,
    parameter integer INDEX = 0
) (
    input  wire en,
    output wire out
);

`ifdef SIM
    localparam real HALF_NS_NOMINAL = 2.0;

    // Same function as half_ns() in tb/ropuf/test_ropuf.py.
    function real half_ns(input integer idx);
        reg [63:0] p;
        integer h;
        begin
            p = idx * 64'd2654435761;
            h = p[31:8] % 4001;
            half_ns = HALF_NS_NOMINAL * (1.0 + 0.00001 * (h - 2000));
        end
    endfunction

    real half;
    reg  osc = 1'b0;
    initial half = half_ns(INDEX);

    always begin
        if (en) begin
            #(half) osc = en ? ~osc : 1'b0;
        end else begin
            osc = 1'b0;
            @(posedge en);
        end
    end

    assign out = osc;
`else
    (* keep = "true", dont_touch = "true" *)
    wire [N_STAGES-1:0] stage /* synthesis keep */;

    // Each stage is its own preserved instance; keep attributes on the
    // nets alone do not stop every tool from cancelling inverter pairs.
    (* keep = "true", dont_touch = "true" *)
    ro_stage #(.NAND(1)) u_nand (.a(stage[N_STAGES-1]), .en(en), .y(stage[0]));
    genvar k;
    generate
        for (k = 1; k < N_STAGES; k = k + 1) begin : g_inv
            (* keep = "true", dont_touch = "true" *)
            ro_stage #(.NAND(0)) u_inv (.a(stage[k-1]), .en(1'b1), .y(stage[k]));
        end
    endgenerate

    assign out = stage[N_STAGES-1] & en;
`endif

endmodule

// One RO stage: NAND with enable, or an inverter. Kept as a separate,
// unflattened cell so synthesis cannot merge stages.
(* keep_hierarchy = "yes", dont_touch = "true" *)
module ro_stage #(
    parameter integer NAND = 0
) (
    input  wire a,
    input  wire en,
    output wire y
);
    generate
        if (NAND) begin : g_nand
            assign y = ~(a & en);
        end else begin : g_inv
            assign y = ~a;
        end
    endgenerate
endmodule

`default_nettype wire
