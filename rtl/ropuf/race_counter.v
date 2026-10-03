// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Counter clocked by one ring oscillator. Counts until it reaches 2^LOG2N
// (bit LOG2N set) or until the other counter's `done` arrives through a
// two-flop synchronizer in this clock domain, whichever is first; it then
// holds its value and raises `halted`.
//
// `clr` is asynchronous and must only be asserted/released while the RO is
// disabled (no clock edges), which ropuf_core guarantees.
module race_counter #(
    parameter integer LOG2N = 14
) (
    input  wire             clk_ro,
    input  wire             clr,
    input  wire             other_done,   // async, from the other RO domain
    output reg  [LOG2N:0]   count,
    output wire             done,         // reached 2^LOG2N
    output wire             halted        // stopped counting (done or stopped)
);

    (* ASYNC_REG = "TRUE" *) reg [1:0] stop_sync;

    assign done   = count[LOG2N];
    assign halted = done | stop_sync[1];

    always @(posedge clk_ro or posedge clr) begin
        if (clr) begin
            count     <= {(LOG2N+1){1'b0}};
            stop_sync <= 2'b00;
        end else begin
            stop_sync <= {stop_sync[0], other_done};
            if (!halted)
                count <= count + 1'b1;
        end
    end

endmodule

`default_nettype wire
