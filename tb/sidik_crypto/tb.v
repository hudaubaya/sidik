// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Testbench for rtl/sidik_crypto.v: 50 MHz clock, and a monitor of the
// Shaman interface protocol. proto_err counts violations of:
//   - clockinData high for more than one cycle;
//   - clockinData high while the core is busy;
//   - a byte strobe on the GAP_MIN cycles after the 64th byte of a block
//     (busy rises one cycle late);
//   - resultNext high for more than one cycle;
//   - start or a strobe while the core is in reset.
module tb;
    localparam integer GAP_MIN = 2;

    reg clk = 1'b0;
    always #10 clk = ~clk;

    reg          rst = 1'b1;
    reg          start = 1'b0;
    reg  [1:0]   op = 2'd0;
    reg  [215:0] key_bits = 216'd0;
    reg  [255:0] challenge = 256'd0;
    reg          clear = 1'b0;
    wire         busy, done, error, k_valid;
    wire [255:0] result;

    sidik_crypto u_crypto (
        .clk(clk), .rst(rst), .start(start), .op(op), .key_bits(key_bits),
        .challenge(challenge), .clear(clear), .busy(busy), .done(done),
        .error(error), .result(result), .k_valid(k_valid));

    // ---- protocol monitor ------------------------------------------------------
    wire clockin  = u_crypto.sh_clockin;
    wire next     = u_crypto.sh_next;
    wire sh_start = u_crypto.sh_start;
    wire core_rst = u_crypto.core_rst;
    wire sh_busy  = u_crypto.sh_busy;

    reg        clockin_d = 1'b0, next_d = 1'b0;
    reg [6:0]  nbytes = 7'd0;        // strobes in the current block
    reg [7:0]  since64 = 8'hff;      // cycles since the 64th strobe
    reg [31:0] proto_err = 32'd0;
    reg [31:0] n_strobes = 32'd0;

    always @(posedge clk) begin
        clockin_d <= clockin;
        next_d    <= next;
        if (since64 != 8'hff)
            since64 <= since64 + 1'b1;
        if (core_rst)
            nbytes <= 7'd0;
        if (clockin && !clockin_d) begin
            n_strobes <= n_strobes + 1'b1;
            if (sh_busy || core_rst || (since64 != 8'hff && since64 <= GAP_MIN)) begin
                proto_err <= proto_err + 1'b1;
                $display("%t protocol: strobe while busy / in reset / right after byte 64", $time);
            end
            if (nbytes == 7'd63) begin
                nbytes  <= 7'd0;
                since64 <= 8'd0;
            end else begin
                nbytes  <= nbytes + 1'b1;
                since64 <= 8'hff;
            end
        end
        if (clockin && clockin_d) begin
            proto_err <= proto_err + 1'b1;
            $display("%t protocol: clockinData high for two cycles", $time);
        end
        if (next && next_d) begin
            proto_err <= proto_err + 1'b1;
            $display("%t protocol: resultNext high for two cycles", $time);
        end
        if (sh_start && core_rst) begin
            proto_err <= proto_err + 1'b1;
            $display("%t protocol: start while the core is in reset", $time);
        end
    end

endmodule

`default_nettype wire
