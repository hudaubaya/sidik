// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Fuzzy extractor for the RO-PUF: enrollment and reconstruction as in
// model/ro_puf.py (enroll_from_mean / reconstruct), using rtl/secded72.v as
// the syndrome secure sketch.
//
// Measurements come from outside through a request / acknowledge port
// (meas_req + meas_pair -> one-cycle meas_ack + meas_delta), e.g. from
// rtl/ropuf/ropuf_core.v: delta = count(RO 2*pair) - count(RO 2*pair+1).
// Each request is one race. The response bit of a race is delta > 0.
//
// Enrollment (cmd_enroll; one phase per enrollment temperature)
//   For every pair 0..N_PAIRS-1: N_ENROLL races, sum S.
//     pass = |S| >= N_ENROLL * tau   (i.e. |mean delta| >= tau)
//     bit  = S > 0
//   enroll_first starts a new enrollment; later phases keep a pair only if
//   it passed in every phase with the same bit (model: enroll_temps_c).
//   The key bit is the bit of the first phase. In the phase with
//   enroll_last, the first N_KEY passing pairs (in pair order) are
//   selected: helper_mask marks them, key bit i = bit of the i-th selected
//   pair, helper_syn = SECDED syndrome of each 72-bit block. Fewer than
//   N_KEY passing pairs -> fail. On success the key is presented
//   (key_valid) until key_ack, so that the consumer can derive K and the
//   key-check value (KCV) and store the helper data.
//
// Reconstruction (cmd_recon, with helper_mask_in / helper_syn_in)
//   Round 0: N_VOTES races per selected pair, majority vote -> key bit;
//   each block decoded with secded72 (1 bit corrected, 2 bits detected).
//   If every block decoded, the key is presented for the key check: the
//   consumer recomputes the KCV and answers key_ack with key_good. A
//   detected block is re-measured (N_VOTES races per pair of that block);
//   a KCV mismatch re-measures every block. At most MAX_REMEASURE such
//   rounds; then fail. Without a KCV the consumer ties key_good to 1, and
//   a SECDED miscorrection gives a wrong key silently (model kcv_bits = 0).
//   A helper mask with a population count other than N_KEY fails at once.
//
// Buffer erasure
//   Response bits (sign_q, pass_q), the key buffer (key_q), the race sum
//   and the vote counter are cleared as soon as they are no longer needed:
//   after key_ack, on every failure, and on cmd_abort. Between the phases
//   of a multi-phase enrollment sign_q / pass_q must survive; cmd_abort
//   clears them if the enrollment is not finished, and so does any
//   reconstruction started in between (a pending enrollment is then lost).
//   The key output is forced to 0 whenever key_valid is low.
//   zeroize (asynchronous, e.g. tamper) clears every register at once; an
//   illegal FSM state erases like a failure.
//
// Status: busy while working; done (with fail) stays high until the next
// command. attempts = 1 + re-measurement rounds, kcv_caught = a KCV
// mismatch occurred, n_pass = pairs passing the mask (last enrollment).
module fuzzy_ext #(
    parameter integer N_PAIRS       = 512,
    parameter integer N_KEY         = 216,   // multiple of 72
    parameter integer N_ENROLL      = 16,
    parameter integer N_VOTES       = 3,     // odd
    parameter integer MAX_REMEASURE = 3,
    parameter integer DW            = 16,    // measurement width (signed)
    parameter integer PW = $clog2(N_PAIRS),
    parameter integer NB = N_KEY / 72        // blocks
) (
    input  wire                 clk,
    input  wire                 rst,
    input  wire                 zeroize,      // async: erase everything now

    input  wire                 cmd_enroll,
    input  wire                 cmd_recon,
    input  wire                 cmd_abort,
    input  wire                 enroll_first,
    input  wire                 enroll_last,
    input  wire [DW-1:0]        tau,          // mask threshold, counts

    input  wire [N_PAIRS-1:0]   helper_mask_in,
    input  wire [8*NB-1:0]      helper_syn_in,
    output reg  [N_PAIRS-1:0]   helper_mask,
    output reg  [8*NB-1:0]      helper_syn,

    output wire                 meas_req,
    output wire [PW-1:0]        meas_pair,
    input  wire                 meas_ack,
    input  wire signed [DW-1:0] meas_delta,

    output wire                 key_valid,
    output wire [N_KEY-1:0]     key,
    input  wire                 key_ack,
    input  wire                 key_good,

    output wire                 busy,
    output reg                  done,
    output reg                  fail,
    output reg  [2:0]           attempts,
    output reg                  kcv_caught,
    output reg  [PW:0]          n_pass
);

    localparam integer SW = DW + $clog2(N_ENROLL) + 1;   // race sum width
    localparam integer KW = $clog2(N_KEY + 1);
    localparam integer CW = $clog2(N_ENROLL + 1);

    localparam [3:0] S_IDLE    = 4'd0,
                     S_E_REQ   = 4'd1,   // enrollment race
                     S_E_ACC   = 4'd2,
                     S_E_SYN   = 4'd3,   // syndromes of the key blocks
                     S_R_SCAN  = 4'd4,   // next selected pair to measure
                     S_R_REQ   = 4'd5,   // reconstruction race
                     S_R_ACC   = 4'd6,
                     S_R_DEC   = 4'd7,   // decode re-measured blocks
                     S_R_CHECK = 4'd8,
                     S_KEY     = 4'd9,   // key presented, wait key_ack
                     S_ZERO    = 4'd10;  // erase buffers, then done

    reg [3:0]           state;
    reg                 recon;           // operation is a reconstruction
    reg                 first_q, last_q;
    reg [DW-1:0]        tau_q;
    reg [PW:0]          pair;            // 0..N_PAIRS
    reg [CW-1:0]        k;               // races taken for the current pair
    reg signed [SW-1:0] acc;             // race sum (enrollment)
    reg [CW-1:0]        votes;           // races with delta > 0 (reconstruction)
    reg [N_PAIRS-1:0]   sign_q;          // response bit per pair (first phase)
    reg [N_PAIRS-1:0]   pass_q;          // passing so far
    reg [N_KEY-1:0]     key_q;
    reg [KW-1:0]        sel;             // selected pairs so far / key bit index
    reg [NB-1:0]        redo;            // blocks measured in this round
    reg [NB-1:0]        det;             // blocks left uncorrectable
    reg [1:0]           blk;
    reg [2:0]           round;
    reg                 fail_next;

    assign busy      = state != S_IDLE;
    assign meas_req  = state == S_E_REQ || state == S_R_REQ;
    assign meas_pair = pair[PW-1:0];
    assign key_valid = state == S_KEY;
    assign key       = key_valid ? key_q : {N_KEY{1'b0}};

    // --- SECDED on one block ------------------------------------------------
    wire [71:0] blk_word   = key_q[72*blk +: 72];
    wire [7:0]  blk_helper = recon ? helper_syn[8*blk +: 8] : 8'd0;
    wire [7:0]  blk_syn;
    wire [71:0] blk_corr;
    wire        blk_single, blk_unc;

    secded72 u_secded (
        .word(blk_word), .helper(blk_helper), .syndrome(blk_syn),
        .corrected(blk_corr), .single_error(blk_single), .uncorrectable(blk_unc));

    // --- enrollment decision for the current pair ---------------------------
    wire [SW-1:0] acc_abs   = acc[SW-1] ? -acc : acc;
    wire [SW+DW-1:0] thresh = tau_q * N_ENROLL;
    wire          pass_now  = {{DW{1'b0}}, acc_abs} >= thresh;
    wire          sign_now  = !acc[SW-1] && acc != 0;
    wire [PW-1:0] p         = pair[PW-1:0];
    wire          pass_all  = first_q ? pass_now
                                      : pass_q[p] && pass_now && sign_q[p] == sign_now;
    wire          bit_all   = first_q ? sign_now : sign_q[p];

    // --- reconstruction: block of the current key bit ------------------------
    wire [1:0]    sel_blk   = sel >= 144 ? 2'd2 : sel >= 72 ? 2'd1 : 2'd0;
    wire          sel_here  = helper_mask[p] && redo[sel_blk];

    function integer popcount(input [N_PAIRS-1:0] v);
        integer i;
        begin
            popcount = 0;
            for (i = 0; i < N_PAIRS; i = i + 1)
                popcount = popcount + v[i];
        end
    endfunction

    // Every register of the FSM to its reset value (async zeroize and rst).
    task clear_all;
        begin
            state       <= S_IDLE;
            recon       <= 1'b0;
            first_q     <= 1'b0;
            last_q      <= 1'b0;
            tau_q       <= {DW{1'b0}};
            pair        <= {(PW+1){1'b0}};
            k           <= {CW{1'b0}};
            acc         <= {SW{1'b0}};
            votes       <= {CW{1'b0}};
            sign_q      <= {N_PAIRS{1'b0}};
            pass_q      <= {N_PAIRS{1'b0}};
            key_q       <= {N_KEY{1'b0}};
            sel         <= {KW{1'b0}};
            redo        <= {NB{1'b0}};
            det         <= {NB{1'b0}};
            blk         <= 2'd0;
            round       <= 3'd0;
            fail_next   <= 1'b0;
            helper_mask <= {N_PAIRS{1'b0}};
            helper_syn  <= {8*NB{1'b0}};
            done        <= 1'b0;
            fail        <= 1'b0;
            attempts    <= 3'd0;
            kcv_caught  <= 1'b0;
            n_pass      <= {(PW+1){1'b0}};
        end
    endtask

    always @(posedge clk or posedge zeroize) begin
        if (zeroize) begin
            clear_all;
        end else if (rst) begin
            clear_all;
        end else if (cmd_abort) begin
            fail_next <= 1'b1;
            state     <= S_ZERO;
        end else begin
            case (state)
                S_IDLE: begin
                    if (cmd_enroll) begin
                        recon     <= 1'b0;
                        first_q   <= enroll_first;
                        last_q    <= enroll_last;
                        tau_q     <= tau;
                        pair      <= {(PW+1){1'b0}};
                        k         <= {CW{1'b0}};
                        acc       <= {SW{1'b0}};
                        sel       <= {KW{1'b0}};
                        n_pass    <= {(PW+1){1'b0}};
                        done      <= 1'b0;
                        fail      <= 1'b0;
                        attempts  <= 3'd0;
                        kcv_caught <= 1'b0;
                        if (enroll_last) begin
                            helper_mask <= {N_PAIRS{1'b0}};
                            helper_syn  <= {8*NB{1'b0}};
                        end
                        state <= S_E_REQ;
                    end else if (cmd_recon) begin
                        recon       <= 1'b1;
                        helper_mask <= helper_mask_in;
                        helper_syn  <= helper_syn_in;
                        done        <= 1'b0;
                        fail        <= 1'b0;
                        attempts    <= 3'd1;
                        kcv_caught  <= 1'b0;
                        round       <= 3'd0;
                        redo        <= {NB{1'b1}};
                        det         <= {NB{1'b0}};
                        pair        <= {(PW+1){1'b0}};
                        sel         <= {KW{1'b0}};
                        if (popcount(helper_mask_in) != N_KEY) begin
                            fail_next <= 1'b1;
                            state     <= S_ZERO;
                        end else begin
                            state <= S_R_SCAN;
                        end
                    end
                end

                // ---------------- enrollment ----------------
                S_E_REQ: begin
                    if (meas_ack) begin
                        acc   <= acc + meas_delta;
                        k     <= k + 1'b1;
                        state <= S_E_ACC;
                    end
                end

                S_E_ACC: begin
                    if (k != N_ENROLL) begin
                        state <= S_E_REQ;
                    end else begin
                        if (!last_q) begin
                            if (first_q)
                                sign_q[p] <= sign_now;
                            pass_q[p] <= pass_all;
                        end else begin
                            if (pass_all) begin
                                n_pass <= n_pass + 1'b1;
                                if (sel != N_KEY) begin
                                    helper_mask[p] <= 1'b1;
                                    key_q[sel]     <= bit_all;
                                    sel            <= sel + 1'b1;
                                end
                            end
                            // ZEROIZE: response bits of this pair are consumed.
                            sign_q[p] <= 1'b0;
                            pass_q[p] <= 1'b0;
                        end
                        acc  <= {SW{1'b0}};
                        k    <= {CW{1'b0}};
                        pair <= pair + 1'b1;
                        if (pair == N_PAIRS - 1) begin
                            if (!last_q) begin
                                state <= S_IDLE;     // phase done, keep sign_q/pass_q
                                done  <= 1'b1;
                            end else if (sel != N_KEY) begin
                                fail_next <= 1'b1;
                                state     <= S_ZERO;
                            end else begin
                                blk   <= 2'd0;
                                state <= S_E_SYN;
                            end
                        end else begin
                            state <= S_E_REQ;
                        end
                    end
                end

                S_E_SYN: begin
                    helper_syn[8*blk +: 8] <= blk_syn;
                    if (blk == NB - 1) begin
                        state <= S_KEY;
                    end else begin
                        blk <= blk + 1'b1;
                    end
                end

                // ---------------- reconstruction ----------------
                S_R_SCAN: begin
                    if (pair == N_PAIRS) begin
                        blk   <= 2'd0;
                        state <= S_R_DEC;
                    end else if (sel_here) begin
                        votes <= {CW{1'b0}};
                        k     <= {CW{1'b0}};
                        state <= S_R_REQ;
                    end else begin
                        if (helper_mask[p])
                            sel <= sel + 1'b1;
                        pair <= pair + 1'b1;
                    end
                end

                S_R_REQ: begin
                    if (meas_ack) begin
                        votes <= votes + (meas_delta > 0);
                        k     <= k + 1'b1;
                        state <= S_R_ACC;
                    end
                end

                S_R_ACC: begin
                    if (k != N_VOTES) begin
                        state <= S_R_REQ;
                    end else begin
                        key_q[sel] <= votes > N_VOTES / 2;
                        votes      <= {CW{1'b0}};   // ZEROIZE: vote count consumed
                        k          <= {CW{1'b0}};
                        sel        <= sel + 1'b1;
                        pair       <= pair + 1'b1;
                        state      <= S_R_SCAN;
                    end
                end

                S_R_DEC: begin
                    if (redo[blk]) begin
                        key_q[72*blk +: 72] <= blk_corr;
                        det[blk]            <= blk_unc;
                    end
                    if (blk == NB - 1)
                        state <= S_R_CHECK;
                    else
                        blk <= blk + 1'b1;
                end

                S_R_CHECK: begin
                    if (det == {NB{1'b0}}) begin
                        state <= S_KEY;          // key check by the consumer
                    end else if (round != MAX_REMEASURE) begin
                        redo     <= det;
                        round    <= round + 1'b1;
                        attempts <= attempts + 1'b1;
                        pair     <= {(PW+1){1'b0}};
                        sel      <= {KW{1'b0}};
                        state    <= S_R_SCAN;
                    end else begin
                        fail_next <= 1'b1;
                        state     <= S_ZERO;
                    end
                end

                // ---------------- key presented ----------------
                S_KEY: begin
                    if (key_ack) begin
                        if (!recon || key_good) begin
                            fail_next <= 1'b0;
                            state     <= S_ZERO;
                        end else begin
                            kcv_caught <= 1'b1;
                            if (round != MAX_REMEASURE) begin
                                redo     <= {NB{1'b1}};
                                round    <= round + 1'b1;
                                attempts <= attempts + 1'b1;
                                pair     <= {(PW+1){1'b0}};
                                sel      <= {KW{1'b0}};
                                state    <= S_R_SCAN;
                            end else begin
                                fail_next <= 1'b1;
                                state     <= S_ZERO;
                            end
                        end
                    end
                end

                // ---------------- erase and finish ----------------
                S_ZERO: begin
                    key_q  <= {N_KEY{1'b0}};       // ZEROIZE: key buffer
                    sign_q <= {N_PAIRS{1'b0}};     // ZEROIZE: response bits
                    pass_q <= {N_PAIRS{1'b0}};
                    acc    <= {SW{1'b0}};
                    votes  <= {CW{1'b0}};
                    k      <= {CW{1'b0}};
                    sel    <= {KW{1'b0}};
                    det    <= {NB{1'b0}};
                    redo   <= {NB{1'b0}};
                    pair   <= {(PW+1){1'b0}};
                    if (fail_next && !recon) begin
                        helper_mask <= {N_PAIRS{1'b0}};
                        helper_syn  <= {8*NB{1'b0}};
                    end
                    fail      <= fail_next;
                    done      <= 1'b1;
                    fail_next <= 1'b0;
                    state     <= S_IDLE;
                end

                // Illegal state: erase like a failure.
                default: begin
                    fail_next <= 1'b1;
                    state     <= S_ZERO;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
