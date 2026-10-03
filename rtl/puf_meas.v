// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Measures one RO pair: two counters race until the first reaches
// 2^LOG2N RO cycles; reports bit = sign of delta and mag = |delta|, with
// delta = count_a - count_b in RO cycles. All outputs are in the `clk`
// domain (50 MHz).
//
// High-frequency safety: each RO clocks only a PRESCALE_LOG2-stage ripple
// divider (one toggle flip-flop per stage). The counter itself runs at
// f_RO / 2^PRESCALE_LOG2, so it needs half the fmax of a counter clocked by
// the RO directly (PRESCALE_LOG2 = 1). Counts are therefore in steps of
// 2^PRESCALE_LOG2 RO cycles.
//
// The faster counter stops at exactly 2^LOG2N RO cycles in its own domain.
// Its done flag reaches the slower counter through a 2-flop synchronizer in
// the slower counter's (divided) domain, so the slower one runs 2 more
// divided cycles. Halted flags reach `clk` through 2-flop synchronizers;
// counts are static once halted, so capturing them then is safe.
//
// Sequence: CLEAR (ROs off, counters in async reset) -> ARM (reset
// released, ROs still off) -> RUN (ro_run = 1) until both halted or
// `timeout` clk cycles -> STOP (ro_run = 0, capture).
module puf_meas #(
    parameter integer LOG2N = 14,
    parameter integer PRESCALE_LOG2 = 1
) (
    input  wire              clk,
    input  wire              rst,
    input  wire              start,
    input  wire [31:0]       timeout,
    input  wire              ro_a,
    input  wire              ro_b,
    output reg               ro_run,     // enable for the selected pair
    output reg               busy,
    output reg               done,       // last measurement finished
    output reg               timed_out,  // last measurement timed out
    output reg               bit_out,    // 1 when RO a is faster (delta > 0)
    output reg  [LOG2N:0]    mag,        // |delta|, RO cycles
    output reg  [LOG2N:0]    count_a,    // RO cycles
    output reg  [LOG2N:0]    count_b
);

    localparam integer P = PRESCALE_LOG2;
    localparam integer CW = LOG2N - P;  // counter counts to 2^CW divided cycles

    reg              cnt_clr;
    wire [CW:0]      cnt_a, cnt_b;
    wire             done_a, done_b, halted_a, halted_b;

    puf_race_counter #(.CW(CW), .P(P)) u_cnt_a (
        .ro(ro_a), .clr(cnt_clr), .other_done(done_b),
        .count(cnt_a), .done(done_a), .halted(halted_a));
    puf_race_counter #(.CW(CW), .P(P)) u_cnt_b (
        .ro(ro_b), .clr(cnt_clr), .other_done(done_a),
        .count(cnt_b), .done(done_b), .halted(halted_b));

    (* ASYNC_REG = "TRUE" *) reg [1:0] halted_a_sync, halted_b_sync;
    always @(posedge clk) begin
        halted_a_sync <= {halted_a_sync[0], halted_a};
        halted_b_sync <= {halted_b_sync[0], halted_b};
    end

    // Counts in RO cycles (zero-extended to LOG2N+1 bits, then scaled).
    wire [LOG2N:0] cnt_a_w = cnt_a;
    wire [LOG2N:0] cnt_b_w = cnt_b;
    wire [LOG2N:0] ra = cnt_a_w << P;
    wire [LOG2N:0] rb = cnt_b_w << P;

    localparam [2:0] S_IDLE = 3'd0, S_CLEAR = 3'd1, S_ARM = 3'd2,
                     S_RUN = 3'd3, S_STOP = 3'd4;
    reg [2:0]  state;
    reg [31:0] timer;

    always @(posedge clk) begin
        if (rst) begin
            state     <= S_IDLE;
            ro_run    <= 1'b0;
            cnt_clr   <= 1'b1;
            busy      <= 1'b0;
            done      <= 1'b0;
            timed_out <= 1'b0;
            timer     <= 32'd0;
            bit_out   <= 1'b0;
            mag       <= {(LOG2N+1){1'b0}};
            count_a   <= {(LOG2N+1){1'b0}};
            count_b   <= {(LOG2N+1){1'b0}};
        end else begin
            case (state)
                S_IDLE: if (start) begin
                    busy      <= 1'b1;
                    done      <= 1'b0;
                    timed_out <= 1'b0;
                    cnt_clr   <= 1'b1;
                    timer     <= 32'd0;
                    state     <= S_CLEAR;
                end
                S_CLEAR: begin
                    timer <= timer + 1;
                    if (timer == 32'd3) begin
                        cnt_clr <= 1'b0;
                        timer   <= 32'd0;
                        state   <= S_ARM;
                    end
                end
                S_ARM: begin
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
                    count_a <= ra;
                    count_b <= rb;
                    bit_out <= ra > rb;
                    mag     <= (ra > rb) ? ra - rb : rb - ra;
                    busy    <= 1'b0;
                    done    <= 1'b1;
                    state   <= S_IDLE;
                end
                default: state <= S_IDLE;
            endcase
        end
    end

endmodule

// One racing counter. `ro` clocks only a P-stage ripple divider; the
// counter runs on the divided clock and stops at 2^CW divided cycles or two
// divided cycles after `other_done` (async) arrives. `clr` is asynchronous
// and is only toggled while the RO is off.
module puf_race_counter #(
    parameter integer CW = 13,
    parameter integer P = 1
) (
    input  wire          ro,
    input  wire          clr,
    input  wire          other_done,
    output reg  [CW:0]   count,
    output wire          done,
    output wire          halted
);

    wire div_clk;

    generate
        if (P == 0) begin : g_nodiv
            assign div_clk = ro;
        end else begin : g_div
            // Ripple divider: stage 0 toggles on every RO edge, stage k on
            // every falling edge of stage k-1. div_clk = f_RO / 2^P.
            reg [P-1:0] pre;
            always @(posedge ro or posedge clr)
                if (clr) pre[0] <= 1'b0;
                else     pre[0] <= ~pre[0];
            genvar k;
            for (k = 1; k < P; k = k + 1) begin : g_stage
                always @(negedge pre[k-1] or posedge clr)
                    if (clr) pre[k] <= 1'b0;
                    else     pre[k] <= ~pre[k];
            end
            assign div_clk = pre[P-1];
        end
    endgenerate

    (* ASYNC_REG = "TRUE" *) reg [1:0] stop_sync;

    assign done   = count[CW];
    assign halted = done | stop_sync[1];

    always @(posedge div_clk or posedge clr) begin
        if (clr) begin
            count     <= {(CW+1){1'b0}};
            stop_sync <= 2'b00;
        end else begin
            stop_sync <= {stop_sync[0], other_done};
            if (!halted)
                count <= count + 1'b1;
        end
    end

endmodule

`default_nettype wire
