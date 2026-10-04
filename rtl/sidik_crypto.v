// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// SIDIK key derivation and HMAC-SHA256 on the unmodified Shaman SHA-256
// core (rtl/third_party/shaman). Same functions as model/ro_puf.py:
//
//   OP_DERIVE  K   = SHA-256(216 key bits, MSB first || "SIDIK-K")
//   OP_HMAC    tag = HMAC-SHA256(K, challenge)      challenge = 32 bytes
//   OP_ID      ID  = HMAC-SHA256(K, "SIDIK-ID")
//   OP_KCV     HMAC-SHA256(K, "SIDIK-CHK"); the key-check value is the
//              first 4 bytes (result[255:224])
//
// K stays inside (k_q) and is only used by the HMAC operations; `clear`
// erases K and the result. Byte order: byte 0 of a 32-byte value is bits
// [255:248]. key_bits[j] is key bit j (as rtl/fuzzy_ext.v outputs it); key
// byte i holds bits 8i..8i+7 with bit 8i as its MSB (numpy.packbits).
//
// Every message has a fixed length, so the SHA-256 padding is constant:
//   derive:        1 block  (34 bytes)
//   HMAC inner:    2 blocks (K ^ ipad, then message + padding)
//   HMAC outer:    2 blocks (K ^ opad, then inner digest + padding)
//
// Shaman protocol (byte-serial mode, parallelLoading = 0):
//   - each hash: core reset, then `start` for one cycle;
//   - each data byte: clockinData high one cycle, low one cycle;
//   - after the 64th byte of a block, no byte on the next cycles: busy
//     rises one cycle late. The wrapper waits GAP cycles, then for busy to
//     drop (and, after the last block, for resultReady);
//   - digest: byte 0 is read when the result is ready; for each next byte
//     resultNext is high one cycle, low one cycle, and resultbyteOut is
//     captured in the cycle after that;
//   - after every hash the core is held in reset, which clears its message
//     buffer, schedule and state.
//
// Fixed latency: `done` pulses exactly LAT_DERIVE / LAT_HMAC cycles after
// the `start` cycle, whatever the data. If the core were ever slower,
// `error` is set (never expected; the core's timing is data independent).
// An HMAC operation without a derived K finishes at the same cycle with
// error set and a zero result.
//
// zeroize (asynchronous, e.g. tamper) clears every register at once and
// holds the Shaman core in reset (its state clears on the next clock edge).
// An illegal FSM state erases K and all intermediate values.
module sidik_crypto #(
    parameter integer RST_CYCLES = 2,
    parameter integer GAP        = 2,
    parameter integer LAT_DERIVE = 777,
    parameter integer LAT_HMAC   = 2833
) (
    input  wire         clk,
    input  wire         rst,
    input  wire         zeroize,    // async: erase K and all state now
    input  wire         start,
    input  wire [1:0]   op,
    input  wire [215:0] key_bits,
    input  wire [255:0] challenge,
    input  wire         clear,
    output wire         busy,
    output reg          done,
    output reg          error,
    output reg  [255:0] result,
    output reg          k_valid
);

    localparam [1:0] OP_DERIVE = 2'd0, OP_HMAC = 2'd1, OP_ID = 2'd2, OP_KCV = 2'd3;

    // ---- Shaman core ---------------------------------------------------------
    reg        core_rst;
    reg        sh_start, sh_clockin, sh_next;
    reg  [7:0] sh_data;
    wire [7:0] sh_out, sh_uio_out, sh_uio_oe;
    wire       sh_ready = sh_uio_out[0];
    wire       sh_busy  = sh_uio_out[4];

    tt_um_psychogenic_shaman u_shaman (
        .ui_in  (sh_data),
        .uo_out (sh_out),
        .uio_in ({sh_clockin, sh_start, 2'b00, sh_next, 1'b0, 2'b00}),
        .uio_out(sh_uio_out),
        .uio_oe (sh_uio_oe),
        .ena    (1'b1),
        .clk    (clk),
        .rst_n  (!core_rst)
    );

    // ---- state -----------------------------------------------------------------
    localparam [3:0] S_IDLE   = 4'd0,
                     S_CRST   = 4'd1,   // core in reset
                     S_START  = 4'd2,   // start high
                     S_START2 = 4'd3,   // start low
                     S_HI     = 4'd4,   // clockinData high
                     S_LO     = 4'd5,   // clockinData low
                     S_GAP    = 4'd6,   // no byte after the 64th
                     S_WAIT   = 4'd7,   // block processing
                     S_RCAP   = 4'd8,   // capture a digest byte
                     S_RNEXT  = 4'd9,   // resultNext high
                     S_RLOW   = 4'd10,  // resultNext low
                     S_FRST   = 4'd11,  // final core reset
                     S_HOLD   = 4'd12;  // wait for the fixed latency

    reg [3:0]   state;
    reg [1:0]   op_q;
    reg         pass;          // 0: derive / HMAC inner, 1: HMAC outer
    reg         blk;           // block within the hash
    reg [5:0]   idx;           // byte within the block / digest
    reg [3:0]   wait_n;
    reg [15:0]  cyc;           // cycles since start
    reg [255:0] k_q;           // derived key K
    reg [255:0] inner_q;       // HMAC inner digest
    reg [255:0] dig;           // digest being read out
    reg         fail_q;

    assign busy = state != S_IDLE;

    wire        is_hmac  = op_q != OP_DERIVE;
    wire        last_blk = is_hmac ? blk : 1'b1;
    wire [15:0] lat      = is_hmac ? LAT_HMAC : LAT_DERIVE;

    // ---- message bytes ---------------------------------------------------------
    reg [215:0] kb_rev;        // kb_rev[215-j] = key_bits[j]
    integer j;
    always @* begin
        for (j = 0; j < 216; j = j + 1)
            kb_rev[215 - j] = key_bits[j];
    end

    function [7:0] byte_of(input [255:0] v, input [5:0] i);
        byte_of = v[255 - 8 * i -: 8];
    endfunction

    function [7:0] str_byte(input [71:0] s, input integer len, input [5:0] i);
        // s holds `len` ASCII characters, first character in the top byte
        str_byte = s[8 * len - 1 - 8 * i -: 8];
    endfunction

    reg [7:0] msg;
    always @* begin
        msg = 8'h00;
        if (!is_hmac) begin
            // SHA-256(key bits || "SIDIK-K"), 34 bytes, length 272 bits
            if (idx < 27)       msg = kb_rev[215 - 8 * idx -: 8];
            else if (idx < 34)  msg = str_byte("SIDIK-K", 7, idx - 27);
            else if (idx == 34) msg = 8'h80;
            else if (idx == 62) msg = 8'h01;
            else if (idx == 63) msg = 8'h10;
        end else if (!blk) begin
            // K ^ ipad / K ^ opad
            msg = (idx < 32 ? byte_of(k_q, idx) : 8'h00) ^ (pass ? 8'h5c : 8'h36);
        end else if (pass) begin
            // outer: inner digest, total 96 bytes = 768 bits
            if (idx < 32)       msg = byte_of(inner_q, idx);
            else if (idx == 32) msg = 8'h80;
            else if (idx == 62) msg = 8'h03;
        end else begin
            case (op_q)
                OP_HMAC: begin   // 32-byte challenge, 96 bytes total
                    if (idx < 32)       msg = byte_of(challenge, idx);
                    else if (idx == 32) msg = 8'h80;
                    else if (idx == 62) msg = 8'h03;
                end
                OP_ID: begin     // "SIDIK-ID", 72 bytes total = 576 bits
                    if (idx < 8)        msg = str_byte("SIDIK-ID", 8, idx);
                    else if (idx == 8)  msg = 8'h80;
                    else if (idx == 62) msg = 8'h02;
                    else if (idx == 63) msg = 8'h40;
                end
                default: begin   // "SIDIK-CHK", 73 bytes total = 584 bits
                    if (idx < 9)        msg = str_byte("SIDIK-CHK", 9, idx);
                    else if (idx == 9)  msg = 8'h80;
                    else if (idx == 62) msg = 8'h02;
                    else if (idx == 63) msg = 8'h48;
                end
            endcase
        end
    end

    // ---- control -----------------------------------------------------------------
    // Every register of the FSM to its reset value (async zeroize and rst).
    task clear_all;
        begin
            state      <= S_IDLE;
            core_rst   <= 1'b1;
            sh_start   <= 1'b0;
            sh_clockin <= 1'b0;
            sh_next    <= 1'b0;
            sh_data    <= 8'h00;
            op_q       <= OP_DERIVE;
            pass       <= 1'b0;
            blk        <= 1'b0;
            idx        <= 6'd0;
            wait_n     <= 4'd0;
            cyc        <= 16'd0;
            k_q        <= 256'd0;
            inner_q    <= 256'd0;
            dig        <= 256'd0;
            fail_q     <= 1'b0;
            error      <= 1'b0;
            result     <= 256'd0;
            k_valid    <= 1'b0;
            done       <= 1'b0;
        end
    endtask

    always @(posedge clk or posedge zeroize) begin
        if (zeroize) begin
            clear_all;
        end else if (rst) begin
            clear_all;
        end else begin
            done <= 1'b0;
            cyc <= cyc + 1'b1;
            case (state)
                S_IDLE: begin
                    core_rst <= 1'b1;
                    if (clear) begin
                        k_q     <= 256'd0;        // ERASE: key
                        k_valid <= 1'b0;
                        result  <= 256'd0;
                    end else if (start) begin
                        op_q   <= op;
                        pass   <= 1'b0;
                        blk    <= 1'b0;
                        idx    <= 6'd0;
                        cyc    <= 16'd1;
                        error  <= 1'b0;
                        result <= 256'd0;
                        if (op != OP_DERIVE && !k_valid) begin
                            fail_q <= 1'b1;
                            state  <= S_HOLD;
                        end else begin
                            fail_q <= 1'b0;
                            wait_n <= RST_CYCLES - 1;
                            state  <= S_CRST;
                        end
                    end
                end

                S_CRST: begin
                    core_rst <= 1'b1;
                    if (wait_n == 0) begin
                        core_rst <= 1'b0;
                        state    <= S_START;
                    end else begin
                        wait_n <= wait_n - 1'b1;
                    end
                end

                S_START: begin
                    sh_start <= 1'b1;
                    state    <= S_START2;
                end

                S_START2: begin
                    sh_start <= 1'b0;
                    state    <= S_HI;
                end

                S_HI: begin
                    sh_data    <= msg;
                    sh_clockin <= 1'b1;
                    state      <= S_LO;
                end

                S_LO: begin
                    sh_clockin <= 1'b0;
                    if (idx == 63) begin
                        wait_n <= GAP - 1;
                        state  <= S_GAP;
                    end else begin
                        idx   <= idx + 1'b1;
                        state <= S_HI;
                    end
                end

                S_GAP: begin
                    if (wait_n == 0)
                        state <= S_WAIT;
                    else
                        wait_n <= wait_n - 1'b1;
                end

                S_WAIT: begin
                    if (!sh_busy && (!last_blk || sh_ready)) begin
                        idx <= 6'd0;
                        if (last_blk) begin
                            wait_n <= 4'd1;
                            state  <= S_RCAP;
                        end else begin
                            blk   <= 1'b1;
                            state <= S_HI;
                        end
                    end
                end

                // digest readout: byte idx is in sh_out (wait_n delays byte 0)
                S_RCAP: begin
                    if (wait_n != 0) begin
                        wait_n <= wait_n - 1'b1;
                    end else begin
                        dig[255 - 8 * idx -: 8] <= sh_out;
                        if (idx == 31) begin
                            core_rst <= 1'b1;     // ERASE: core state
                            wait_n   <= RST_CYCLES - 1;
                            state    <= S_FRST;
                        end else begin
                            state <= S_RNEXT;
                        end
                    end
                end

                S_RNEXT: begin
                    sh_next <= 1'b1;
                    state   <= S_RLOW;
                end

                S_RLOW: begin
                    // resultIndex advances at this edge and resultbyteOut one
                    // cycle later: capture one cycle after resultNext.
                    sh_next <= 1'b0;
                    idx     <= idx + 1'b1;
                    wait_n  <= 4'd1;
                    state   <= S_RCAP;
                end

                S_FRST: begin
                    if (wait_n != 0) begin
                        wait_n <= wait_n - 1'b1;
                    end else begin
                        dig <= 256'd0;            // ERASE: readout buffer
                        if (!is_hmac) begin
                            k_q     <= dig;
                            k_valid <= 1'b1;
                            state   <= S_HOLD;
                        end else if (!pass) begin
                            inner_q <= dig;
                            pass    <= 1'b1;
                            blk     <= 1'b0;
                            idx     <= 6'd0;
                            wait_n  <= RST_CYCLES - 1;
                            state   <= S_CRST;
                        end else begin
                            result  <= dig;
                            inner_q <= 256'd0;    // ERASE: inner digest
                            state   <= S_HOLD;
                        end
                    end
                end

                S_HOLD: begin
                    if (cyc >= lat) begin
                        error <= fail_q || cyc != lat;
                        if (fail_q)
                            result <= 256'd0;
                        done  <= 1'b1;
                        state <= S_IDLE;
                    end
                end

                // Illegal state: erase K and every intermediate value.
                default: begin
                    core_rst <= 1'b1;
                    k_q      <= 256'd0;
                    k_valid  <= 1'b0;
                    inner_q  <= 256'd0;
                    dig      <= 256'd0;
                    result   <= 256'd0;
                    error    <= 1'b1;
                    state    <= S_IDLE;
                end
            endcase
        end
    end

endmodule

`default_nettype wire
