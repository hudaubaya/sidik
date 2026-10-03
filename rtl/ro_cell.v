// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
// 1 fs precision so behavioural half-periods (whole femtoseconds) are exact.
`timescale 1ns/1fs

// One ring oscillator: a NAND with the enable followed by N_STAGES-1
// inverters (N_STAGES odd), one LUT per stage. `out` is 0 while `en` is 0.
//
// Three implementations, selected by defines:
//
//   SIM       Behavioural oscillator. The half-period is HALF_PERIOD_NS when
//             that parameter is > 0; otherwise it is a pseudo-random value
//             in 1.94-2.06 ns derived from INDEX and the +RO_SEED=<n>
//             plusarg (see half_fs() in tb/common/ro_sim.py). No jitter.
//   CYCLONEV  Intel Cyclone V: each stage is a LUT followed by an `lcell`
//             primitive, which forces a separate logic cell per stage and
//             stops Quartus from collapsing the loop.
//   (default) Generic: each stage is a separate ro_stage instance with
//             keep / keep_hierarchy / dont_touch (checked with yosys by
//             rtl/ropuf/synth_check.py; for Vivado also set
//             ALLOW_COMBINATORIAL_LOOPS on the stage nets).
//
// Placement of the ROs is not part of this file.
module ro_cell #(
    parameter integer N_STAGES = 5,
    parameter integer INDEX = 0
`ifdef SIM
    // Behavioural model only, so synthesis tools never see a real parameter.
    , parameter real  HALF_PERIOD_NS = 0.0  // 0 = from RO_SEED
`endif
) (
    input  wire en,
    output wire out
);

`ifdef SIM
    // Same function as half_fs() in tb/common/ro_sim.py.
    function [63:0] half_fs(input integer seed, input integer idx);
        reg [63:0] x;
        begin
            x = seed * 64'h9E3779B97F4A7C15 + idx * 64'hBF58476D1CE4E5B9;
            x = x ^ (x >> 31);
            x = x * 64'h94D049BB133111EB;
            x = x ^ (x >> 29);
            half_fs = 64'd1940000 + x % 64'd120001;
        end
    endfunction

    real    half;
    integer seed;
    reg     osc = 1'b0;

    initial begin
        if (HALF_PERIOD_NS > 0.0) begin
            half = HALF_PERIOD_NS;
        end else begin
            if (!$value$plusargs("RO_SEED=%d", seed))
                seed = 1;
            half = half_fs(seed, INDEX) * 1.0e-6;
        end
    end

    always begin
        if (en) begin
            #(half) osc = en ? ~osc : 1'b0;
        end else begin
            osc = 1'b0;
            @(posedge en);
        end
    end

    assign out = osc;

`elsif CYCLONEV
    wire [N_STAGES-1:0] lut  /* synthesis keep */;
    wire [N_STAGES-1:0] stage /* synthesis keep */;

    assign lut[0] = ~(en & stage[N_STAGES-1]);
    lcell u_lc0 (.in(lut[0]), .out(stage[0]));
    genvar k;
    generate
        for (k = 1; k < N_STAGES; k = k + 1) begin : g_inv
            assign lut[k] = ~stage[k-1];
            lcell u_lc (.in(lut[k]), .out(stage[k]));
        end
    endgenerate

    assign out = stage[N_STAGES-1] & en;

`else
    (* keep = "true", dont_touch = "true" *)
    wire [N_STAGES-1:0] stage /* synthesis keep */;

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

// One generic RO stage: NAND with enable, or an inverter. Kept as a
// separate, unflattened cell so synthesis cannot merge stages.
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
