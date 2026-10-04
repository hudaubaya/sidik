// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// SIDIK PUF key generator behind an Avalon-MM slave: rtl/ropuf/ropuf_core.v
// (ring oscillators + race measurement) -> rtl/fuzzy_ext.v (enrollment /
// reconstruction) -> rtl/sidik_crypto.v (K, KCV, ID, HMAC on Shaman).
//
// Register map (32-bit, word addresses avs_address[5:0], read latency 1):
//
//   0       MAGIC   R   0x53444B31 ("SDK1")
//   1       CTRL    W   bit0 ENROLL, bit1 RECONSTRUCT, bit2 AUTH, bit3 CLEAR
//   2       STATUS  R   bit0 BUSY, bit1 DONE, bit2 ERR, bit3 K_READY,
//                       bit4 RECON_FAIL, bit5 TAMPERED
//   3       TAU     RW  mask threshold, counts (enrollment)
//   4..19   HELPER  RW  pair mask, word i = mask[32i+31:32i]
//   20      HELPER  RW  SECDED syndromes, [23:0] (block b in [8b+7:8b])
//   21      HELPER  RW  key-check value KCV = HMAC(K, "SIDIK-CHK")[0:4]
//   22..29  CHAL    RW  challenge, word 0 = bytes 0..3 (big endian)
//   30..37  RESP    R   HMAC(K, CHAL) after AUTH
//   38..45  ID      R   HMAC(K, "SIDIK-ID"), after enrollment or reconstruction
//   48..52  (CHAR_BUILD only) raw race access, see below
//
// HELPER is produced by ENROLL (read it out and store it) and must be
// written back before RECONSTRUCT. HELPER, TAU and CHAL are writable only
// while not busy. A command while busy, AUTH without K, a failed enrollment
// or a timed-out race sets ERR.
//
// Release build (default): there is no read path to K, the key bits, the
// response bits or the raw counter values; they never reach the read mux.
// CHAR_BUILD adds raw race registers for characterization (never ship it):
//   48 RAW_PAIR RW, 49 RAW_CTRL (W bit0 start / R bit0 busy, bit1 timeout),
//   50 COUNT_A, 51 COUNT_B, 52 DELTA.
//
// Erasure:
//   - tamper_n (active low, asynchronous) goes through a 2-flop
//     synchronizer. Its output, the sticky TAMPERED flag and the CLEAR pulse
//     form `zeroize`, an asynchronous clear of K, the key and response
//     buffers (fuzzy_ext, sidik_crypto) and every register of this block.
//     The Shaman core and the race counters clear on the next clock edge
//     (synchronous resets). Everything is erased at most 3 clock edges after
//     tamper_n falls.
//   - TAMPERED stays set, and zeroize asserted, until rst.
//   - CLEAR erases the same state for one cycle; STATUS then reads 0.
//   - An illegal state of this FSM issues CLEAR and sets ERR.
module sidik_avmm #(
    parameter integer N_RO          = 1024,
    parameter integer N_STAGES      = 5,
    parameter integer LOG2N         = 14,
    parameter integer PRESCALE_LOG2 = 1,
    parameter integer N_ENROLL      = 16,     // races averaged per pair at enrollment
    parameter [31:0]  MEAS_TIMEOUT  = 32'd1048576
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        tamper_n,
    input  wire [5:0]  avs_address,
    input  wire        avs_write,
    input  wire [31:0] avs_writedata,
    input  wire        avs_read,
    output reg  [31:0] avs_readdata
);

    localparam [31:0] MAGIC = 32'h53444B31;
    localparam integer N_PAIRS = N_RO / 2;
    localparam integer PAIR_W = $clog2(N_PAIRS);

    localparam [5:0] A_MAGIC = 6'd0, A_CTRL = 6'd1, A_STATUS = 6'd2, A_TAU = 6'd3,
                     A_HELPER = 6'd4, A_SYN = 6'd20, A_KCV = 6'd21,
                     A_CHAL = 6'd22, A_RESP = 6'd30, A_ID = 6'd38,
                     A_RAW_PAIR = 6'd48, A_RAW_CTRL = 6'd49, A_RAW_A = 6'd50,
                     A_RAW_B = 6'd51, A_RAW_D = 6'd52;

    localparam [1:0] OP_DERIVE = 2'd0, OP_HMAC = 2'd1, OP_ID = 2'd2, OP_KCV = 2'd3;

    // ---- tamper, CLEAR, zeroize ----------------------------------------------
    (* ASYNC_REG = "TRUE" *) reg [1:0] tamper_s;
    reg  tampered_q;
    reg  clr_q;              // one-cycle CLEAR pulse (command or illegal state)
    reg  fault_q;            // illegal state seen (reported as ERR)
    wire zeroize = tamper_s[1] | tampered_q | clr_q;

    always @(posedge clk) begin
        if (rst) begin
            tamper_s   <= 2'b00;
            tampered_q <= 1'b0;
        end else begin
            tamper_s <= {tamper_s[0], !tamper_n};
            if (tamper_s[1])
                tampered_q <= 1'b1;
        end
    end

    // ---- submodules ------------------------------------------------------------
    // race measurement
    reg                core_start;
    reg  [PAIR_W-1:0]  core_pair;
    wire               core_busy, core_done, core_timed_out;
    wire [LOG2N:0]     core_count_a, core_count_b;
    wire signed [LOG2N+1:0] core_delta;

    ropuf_core #(.N_RO(N_RO), .N_STAGES(N_STAGES), .LOG2N(LOG2N),
                 .PRESCALE_LOG2(PRESCALE_LOG2)) u_core (
        .clk(clk), .rst(rst | zeroize), .start(core_start), .pair(core_pair),
        .timeout(MEAS_TIMEOUT), .busy(core_busy), .done(core_done),
        .timed_out(core_timed_out), .count_a(core_count_a),
        .count_b(core_count_b), .delta(core_delta));

    wire signed [15:0] core_delta16 = core_delta;

    // fuzzy extractor
    reg          fe_cmd_enroll, fe_cmd_recon, fe_key_ack, fe_key_good;
    reg          fe_meas_ack;
    reg  signed [15:0] fe_meas_delta;
    reg  [15:0]  tau_q;
    reg  [511:0] helper_mask_q;
    reg  [23:0]  helper_syn_q;
    wire [511:0] fe_helper_mask;
    wire [23:0]  fe_helper_syn;
    wire         fe_meas_req, fe_key_valid, fe_busy, fe_done, fe_fail, fe_kcv_caught;
    wire [8:0]   fe_meas_pair;
    wire [215:0] fe_key;
    wire [2:0]   fe_attempts;
    wire [9:0]   fe_n_pass;

    fuzzy_ext #(.N_PAIRS(N_PAIRS), .N_ENROLL(N_ENROLL)) u_fe (
        .clk(clk), .rst(rst), .zeroize(zeroize),
        .cmd_enroll(fe_cmd_enroll), .cmd_recon(fe_cmd_recon), .cmd_abort(1'b0),
        .enroll_first(1'b1), .enroll_last(1'b1), .tau(tau_q),
        .helper_mask_in(helper_mask_q), .helper_syn_in(helper_syn_q),
        .helper_mask(fe_helper_mask), .helper_syn(fe_helper_syn),
        .meas_req(fe_meas_req), .meas_pair(fe_meas_pair), .meas_ack(fe_meas_ack),
        .meas_delta(fe_meas_delta),
        .key_valid(fe_key_valid), .key(fe_key), .key_ack(fe_key_ack),
        .key_good(fe_key_good),
        .busy(fe_busy), .done(fe_done), .fail(fe_fail), .attempts(fe_attempts),
        .kcv_caught(fe_kcv_caught), .n_pass(fe_n_pass));

    // key derivation and HMAC
    reg          cr_start, cr_clear;
    reg  [1:0]   cr_op;
    reg  [255:0] chal_q;
    wire         cr_busy, cr_done, cr_error, cr_k_valid;
    wire [255:0] cr_result;

    sidik_crypto u_crypto (
        .clk(clk), .rst(rst), .zeroize(zeroize), .start(cr_start), .op(cr_op),
        .key_bits(fe_key), .challenge(chal_q), .clear(cr_clear),
        .busy(cr_busy), .done(cr_done), .error(cr_error), .result(cr_result),
        .k_valid(cr_k_valid));

    // ---- control -----------------------------------------------------------------
    localparam [3:0] S_IDLE    = 4'd0,
                     S_E_LAUNCH = 4'd1,   // wait for fuzzy_ext to start
                     S_E_FE    = 4'd2,    // enrollment races
                     S_E_DER   = 4'd3,    // K
                     S_E_KCV   = 4'd4,    // KCV
                     S_E_ID    = 4'd5,    // ID
                     S_E_END   = 4'd6,    // release the key, collect helper data
                     S_R_LAUNCH = 4'd7,
                     S_R_FE    = 4'd8,    // reconstruction races
                     S_R_DER   = 4'd9,
                     S_R_KCV   = 4'd10,
                     S_R_ID    = 4'd11,
                     S_R_END   = 4'd12,
                     S_AUTH    = 4'd13,
                     S_R_ACK   = 4'd14;   // wait for fuzzy_ext to take a rejected key

    reg [3:0]   state;
    reg         cr_issued;
    reg [255:0] resp_q, id_q;
    reg [31:0]  kcv_q;
    reg         done_q, err_q, rfail_q;
    reg [1:0]   m_st;                 // measurement adapter
    reg         m_char;               // current race is a raw (CHAR_BUILD) race
`ifdef CHAR_BUILD
    reg [PAIR_W-1:0] raw_pair_q;
    reg [LOG2N:0]    raw_a_q, raw_b_q;
    reg signed [LOG2N+1:0] raw_d_q;
    reg              raw_busy_q, raw_tout_q, raw_req;
`endif

    wire wr      = avs_write;
    wire wr_ctrl = wr && avs_address == A_CTRL;
    wire cmd_enroll = wr_ctrl && avs_writedata[0];
    wire cmd_recon  = wr_ctrl && avs_writedata[1];
    wire cmd_auth   = wr_ctrl && avs_writedata[2];
    wire cmd_clear  = wr_ctrl && avs_writedata[3];
    wire busy       = state != S_IDLE;

    // CLEAR pulse and the illegal-state fault: outside the zeroize domain.
    reg illegal;   // set by the FSM default branch (zeroize domain)
    always @(posedge clk) begin
        if (rst) begin
            clr_q   <= 1'b0;
            fault_q <= 1'b0;
        end else begin
            clr_q <= cmd_clear || illegal;
            if (illegal)
                fault_q <= 1'b1;
            else if (cmd_enroll || cmd_recon || cmd_auth)
                fault_q <= 1'b0;
        end
    end

    // Start one sidik_crypto operation, then wait for it; returns 1 on done.
    task crypto_step(input [1:0] op);
        begin
            if (!cr_issued) begin
                cr_start  <= 1'b1;
                cr_op     <= op;
                cr_issued <= 1'b1;
            end
        end
    endtask

    task clear_all;
        begin
            state         <= S_IDLE;
            cr_issued     <= 1'b0;
            cr_start      <= 1'b0;
            cr_clear      <= 1'b0;
            cr_op         <= OP_DERIVE;
            fe_cmd_enroll <= 1'b0;
            fe_cmd_recon  <= 1'b0;
            fe_key_ack    <= 1'b0;
            fe_key_good   <= 1'b0;
            fe_meas_ack   <= 1'b0;
            fe_meas_delta <= 16'sd0;
            core_start    <= 1'b0;
            core_pair     <= {PAIR_W{1'b0}};
            m_st          <= 2'd0;
            m_char        <= 1'b0;
            tau_q         <= 16'd0;
            helper_mask_q <= 512'd0;
            helper_syn_q  <= 24'd0;
            kcv_q         <= 32'd0;
            chal_q        <= 256'd0;
            resp_q        <= 256'd0;
            id_q          <= 256'd0;
            done_q        <= 1'b0;
            err_q         <= 1'b0;
            rfail_q       <= 1'b0;
            illegal       <= 1'b0;
`ifdef CHAR_BUILD
            raw_pair_q    <= {PAIR_W{1'b0}};
            raw_a_q       <= {(LOG2N+1){1'b0}};
            raw_b_q       <= {(LOG2N+1){1'b0}};
            raw_d_q       <= {(LOG2N+2){1'b0}};
            raw_busy_q    <= 1'b0;
            raw_tout_q    <= 1'b0;
            raw_req       <= 1'b0;
`endif
        end
    endtask

    integer wi;
    always @(posedge clk or posedge zeroize) begin
        if (zeroize) begin
            clear_all;
        end else if (rst) begin
            clear_all;
        end else begin
            cr_start      <= 1'b0;
            cr_clear      <= 1'b0;
            fe_cmd_enroll <= 1'b0;
            fe_cmd_recon  <= 1'b0;
            fe_key_ack    <= 1'b0;
            fe_meas_ack   <= 1'b0;
            core_start    <= 1'b0;
            illegal       <= 1'b0;

            // ---- bus writes ----
            if (wr && !busy) begin
                if (avs_address == A_TAU)
                    tau_q <= avs_writedata[15:0];
                for (wi = 0; wi < 16; wi = wi + 1)
                    if (avs_address == A_HELPER + wi)
                        helper_mask_q[32 * wi +: 32] <= avs_writedata;
                if (avs_address == A_SYN)
                    helper_syn_q <= avs_writedata[23:0];
                if (avs_address == A_KCV)
                    kcv_q <= avs_writedata;
                for (wi = 0; wi < 8; wi = wi + 1)
                    if (avs_address == A_CHAL + wi)
                        chal_q[255 - 32 * wi -: 32] <= avs_writedata;
`ifdef CHAR_BUILD
                if (avs_address == A_RAW_PAIR)
                    raw_pair_q <= avs_writedata[PAIR_W-1:0];
                if (avs_address == A_RAW_CTRL && avs_writedata[0] && !raw_busy_q) begin
                    raw_req    <= 1'b1;
                    raw_busy_q <= 1'b1;
                end
`endif
            end
            if ((cmd_enroll || cmd_recon || cmd_auth) && busy)
                err_q <= 1'b1;

            // ---- measurement adapter: fuzzy_ext (or a raw race) <-> ropuf_core ----
            case (m_st)
                2'd0: begin
                    if (fe_meas_req && !fe_meas_ack) begin
                        core_pair  <= fe_meas_pair[PAIR_W-1:0];
                        core_start <= 1'b1;
                        m_char     <= 1'b0;
                        m_st       <= 2'd1;
`ifdef CHAR_BUILD
                    end else if (raw_req && !busy) begin
                        raw_req    <= 1'b0;
                        core_pair  <= raw_pair_q;
                        core_start <= 1'b1;
                        m_char     <= 1'b1;
                        m_st       <= 2'd1;
`endif
                    end
                end
                2'd1: if (core_busy) m_st <= 2'd2;
                2'd2: if (!core_busy) begin
                    if (core_timed_out)
                        err_q <= 1'b1;
`ifdef CHAR_BUILD
                    if (m_char) begin
                        raw_a_q    <= core_count_a;
                        raw_b_q    <= core_count_b;
                        raw_d_q    <= core_delta;
                        raw_tout_q <= core_timed_out;
                        raw_busy_q <= 1'b0;
                    end else
`endif
                    begin
                        fe_meas_delta <= core_delta16;
                        fe_meas_ack   <= 1'b1;
                    end
                    m_st <= 2'd3;
                end
                default: m_st <= 2'd0;   // 3: fuzzy_ext drops meas_req
            endcase

            // ---- command FSM ----
            case (state)
                S_IDLE: begin
                    if (cmd_enroll) begin
                        done_q  <= 1'b0;
                        err_q   <= 1'b0;
                        rfail_q <= 1'b0;
                        id_q    <= 256'd0;
                        resp_q  <= 256'd0;
                        kcv_q   <= 32'd0;
                        cr_clear <= 1'b1;
                        fe_cmd_enroll <= 1'b1;
                        state <= S_E_LAUNCH;
                    end else if (cmd_recon) begin
                        done_q  <= 1'b0;
                        err_q   <= 1'b0;
                        rfail_q <= 1'b0;
                        id_q    <= 256'd0;
                        resp_q  <= 256'd0;
                        cr_clear <= 1'b1;
                        fe_cmd_recon <= 1'b1;
                        state <= S_R_LAUNCH;
                    end else if (cmd_auth) begin
                        done_q <= 1'b0;
                        err_q  <= 1'b0;
                        resp_q <= 256'd0;
                        if (!cr_k_valid) begin
                            err_q  <= 1'b1;
                            done_q <= 1'b1;
                        end else begin
                            state <= S_AUTH;
                        end
                    end
                end

                // -------- enrollment --------
                S_E_LAUNCH: if (fe_busy) state <= S_E_FE;
                S_E_FE: begin
                    if (fe_key_valid) begin
                        state <= S_E_DER;
                    end else if (!fe_busy) begin    // fewer than 216 pairs passed
                        err_q  <= 1'b1;
                        done_q <= 1'b1;
                        state  <= S_IDLE;
                    end
                end
                S_E_DER: begin
                    crypto_step(OP_DERIVE);
                    if (cr_done) begin
                        cr_issued <= 1'b0;
                        state     <= S_E_KCV;
                    end
                end
                S_E_KCV: begin
                    crypto_step(OP_KCV);
                    if (cr_done) begin
                        cr_issued <= 1'b0;
                        kcv_q     <= cr_result[255:224];
                        state     <= S_E_ID;
                    end
                end
                S_E_ID: begin
                    crypto_step(OP_ID);
                    if (cr_done) begin
                        cr_issued   <= 1'b0;
                        id_q        <= cr_result;
                        fe_key_ack  <= 1'b1;
                        fe_key_good <= 1'b1;
                        state       <= S_E_END;
                    end
                end
                S_E_END: begin
                    if (!fe_busy) begin
                        helper_mask_q <= fe_helper_mask;
                        helper_syn_q  <= fe_helper_syn;
                        err_q  <= err_q | cr_error | fe_fail;
                        done_q <= 1'b1;
                        state  <= S_IDLE;
                    end
                end

                // -------- reconstruction --------
                S_R_LAUNCH: if (fe_busy) state <= S_R_FE;
                S_R_FE: begin
                    if (fe_key_valid) begin
                        state <= S_R_DER;
                    end else if (!fe_busy) begin
                        // decoded key never matched the KCV, or helper invalid
                        rfail_q  <= fe_fail;
                        cr_clear <= 1'b1;
                        done_q   <= 1'b1;
                        state    <= S_IDLE;
                    end
                end
                S_R_DER: begin
                    crypto_step(OP_DERIVE);
                    if (cr_done) begin
                        cr_issued <= 1'b0;
                        state     <= S_R_KCV;
                    end
                end
                S_R_KCV: begin
                    crypto_step(OP_KCV);
                    if (cr_done) begin
                        cr_issued <= 1'b0;
                        if (cr_result[255:224] == kcv_q) begin
                            state <= S_R_ID;
                        end else begin
                            cr_clear    <= 1'b1;   // K of a wrong key: erase
                            fe_key_ack  <= 1'b1;
                            fe_key_good <= 1'b0;
                            state       <= S_R_ACK;
                        end
                    end
                end
                S_R_ACK: if (!fe_key_valid) state <= S_R_FE;
                S_R_ID: begin
                    crypto_step(OP_ID);
                    if (cr_done) begin
                        cr_issued   <= 1'b0;
                        id_q        <= cr_result;
                        fe_key_ack  <= 1'b1;
                        fe_key_good <= 1'b1;
                        state       <= S_R_END;
                    end
                end
                S_R_END: begin
                    if (!fe_busy) begin
                        err_q  <= err_q | cr_error;
                        done_q <= 1'b1;
                        state  <= S_IDLE;
                    end
                end

                // -------- authentication --------
                S_AUTH: begin
                    crypto_step(OP_HMAC);
                    if (cr_done) begin
                        cr_issued <= 1'b0;
                        resp_q    <= cr_result;
                        err_q     <= cr_error;
                        done_q    <= 1'b1;
                        state     <= S_IDLE;
                    end
                end

                // Illegal state: CLEAR everything (zeroize next cycle).
                default: begin
                    illegal <= 1'b1;
                    state   <= S_IDLE;
                end
            endcase
        end
    end

    // ---- bus reads -----------------------------------------------------------------
    // Only these values reach the read mux. K, key bits, response bits and
    // raw counts have no path here in the release build.
    wire [31:0] status = {26'd0, tampered_q, rfail_q, cr_k_valid,
                          err_q | fault_q, done_q, busy};

    always @(posedge clk) begin
        if (avs_read) begin
            avs_readdata <= 32'd0;
            if (avs_address == A_MAGIC)  avs_readdata <= MAGIC;
            if (avs_address == A_STATUS) avs_readdata <= status;
            if (avs_address == A_TAU)    avs_readdata <= {16'd0, tau_q};
            if (avs_address >= A_HELPER && avs_address < A_HELPER + 16)
                avs_readdata <= helper_mask_q[32 * (avs_address - A_HELPER) +: 32];
            if (avs_address == A_SYN)    avs_readdata <= {8'd0, helper_syn_q};
            if (avs_address == A_KCV)    avs_readdata <= kcv_q;
            if (avs_address >= A_CHAL && avs_address < A_CHAL + 8)
                avs_readdata <= chal_q[255 - 32 * (avs_address - A_CHAL) -: 32];
            if (avs_address >= A_RESP && avs_address < A_RESP + 8)
                avs_readdata <= resp_q[255 - 32 * (avs_address - A_RESP) -: 32];
            if (avs_address >= A_ID && avs_address < A_ID + 8)
                avs_readdata <= id_q[255 - 32 * (avs_address - A_ID) -: 32];
`ifdef CHAR_BUILD
            if (avs_address == A_RAW_PAIR) avs_readdata <= raw_pair_q;
            if (avs_address == A_RAW_CTRL) avs_readdata <= {30'd0, raw_tout_q, raw_busy_q};
            if (avs_address == A_RAW_A)    avs_readdata <= raw_a_q;
            if (avs_address == A_RAW_B)    avs_readdata <= raw_b_q;
            if (avs_address == A_RAW_D)    avs_readdata <= {{(30-LOG2N){raw_d_q[LOG2N+1]}}, raw_d_q};
`endif
        end
    end

endmodule

`default_nettype wire
