// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// RO-PUF measurement core.
//
// N_RO (even) ring oscillators of N_STAGES (odd) stages form disjoint pairs
// (2i, 2i+1). A measurement of
// pair `pair` enables only those two ROs, races a counter on each, and
// reports count_a, count_b and delta = count_a - count_b once both counters
// have halted. The faster RO's counter stops at exactly 2^LOG2N; the slower
// one stops when that event reaches it through a 2-flop synchronizer, so it
// over-counts by a small fixed number of its own cycles (about 2-3).
//
// Sequence (clk domain): CLEAR (ROs off, counters held in reset) -> ARM
// (reset released, ROs still off) -> RUN (pair enabled) until both counters
// report halted, or until `timeout` clk cycles -> STOP (ROs off, results
// captured). Counter values are static once halted, so capturing them after
// the synchronized halted flags is safe. After a timeout the captured counts
// are diagnostic only (the counters may still have been running).
module ropuf_core #(
    parameter integer N_RO = 1024,
    parameter integer N_STAGES = 5,
    parameter integer LOG2N = 14,
    parameter integer PAIR_W = $clog2(N_RO / 2)
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 start,
    input  wire [PAIR_W-1:0]    pair,
    input  wire [31:0]          timeout,
    output reg                  busy,
    output reg                  done,       // last measurement finished
    output reg                  timed_out,  // last measurement timed out
    output reg  [LOG2N:0]       count_a,
    output reg  [LOG2N:0]       count_b,
    output reg  signed [LOG2N+1:0] delta
);

    localparam integer N_PAIRS = N_RO / 2;

    // ---------------------------------------------------------------- ROs
    reg  [PAIR_W-1:0] sel;
    reg               ro_run;
    wire [N_RO-1:0]   ro_out;

    genvar i;
    generate
        for (i = 0; i < N_RO; i = i + 1) begin : g_ro
            ro_cell #(.N_STAGES(N_STAGES), .INDEX(i)) u_ro (
                .en  (ro_run && (sel == i / 2)),
                .out (ro_out[i])
            );
        end
    endgenerate

    wire clk_a = ro_out[{sel, 1'b0}];
    wire clk_b = ro_out[{sel, 1'b1}];

    // ----------------------------------------------------------- counters
    reg              cnt_clr;
    wire [LOG2N:0]   cnt_a, cnt_b;
    wire             done_a, done_b, halted_a, halted_b;

    race_counter #(.LOG2N(LOG2N)) u_cnt_a (
        .clk_ro(clk_a), .clr(cnt_clr), .other_done(done_b),
        .count(cnt_a), .done(done_a), .halted(halted_a));
    race_counter #(.LOG2N(LOG2N)) u_cnt_b (
        .clk_ro(clk_b), .clr(cnt_clr), .other_done(done_a),
        .count(cnt_b), .done(done_b), .halted(halted_b));

    (* ASYNC_REG = "TRUE" *) reg [1:0] halted_a_sync, halted_b_sync;
    always @(posedge clk) begin
        halted_a_sync <= {halted_a_sync[0], halted_a};
        halted_b_sync <= {halted_b_sync[0], halted_b};
    end

    // ---------------------------------------------------------------- FSM
    localparam [2:0] S_IDLE = 3'd0, S_CLEAR = 3'd1, S_ARM = 3'd2,
                     S_RUN = 3'd3, S_STOP = 3'd4;

    reg [2:0]  state;
    reg [31:0] timer;

    always @(posedge clk) begin
        if (rst) begin
            state     <= S_IDLE;
            sel       <= {PAIR_W{1'b0}};
            ro_run    <= 1'b0;
            cnt_clr   <= 1'b1;
            busy      <= 1'b0;
            done      <= 1'b0;
            timed_out <= 1'b0;
            timer     <= 32'd0;
            count_a   <= {(LOG2N+1){1'b0}};
            count_b   <= {(LOG2N+1){1'b0}};
            delta     <= {(LOG2N+2){1'b0}};
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    sel       <= pair;
                    busy      <= 1'b1;
                    done      <= 1'b0;
                    timed_out <= 1'b0;
                    cnt_clr   <= 1'b1;
                    timer     <= 32'd0;
                    state     <= S_CLEAR;
                end
                S_CLEAR: begin
                    // Hold reset for a few cycles with the ROs off.
                    timer <= timer + 1;
                    if (timer == 32'd3) begin
                        cnt_clr <= 1'b0;
                        timer   <= 32'd0;
                        state   <= S_ARM;
                    end
                end
                S_ARM: begin
                    // Let the reset release settle before any RO edge.
                    timer <= timer + 1;
                    if (timer == 32'd3) begin
                        ro_run <= 1'b1;
                        timer  <= 32'd0;
                        state  <= S_RUN;
                    end
                end
                S_RUN: begin
                    timer <= timer + 1;
                    if (halted_a_sync[1] && halted_b_sync[1]) begin
                        ro_run <= 1'b0;
                        state  <= S_STOP;
                    end else if (timer >= timeout) begin
                        ro_run    <= 1'b0;
                        timed_out <= 1'b1;
                        state     <= S_STOP;
                    end
                end
                S_STOP: begin
                    count_a <= cnt_a;
                    count_b <= cnt_b;
                    delta   <= $signed({1'b0, cnt_a}) - $signed({1'b0, cnt_b});
                    busy    <= 1'b0;
                    done    <= 1'b1;
                    state   <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule

`default_nettype wire
