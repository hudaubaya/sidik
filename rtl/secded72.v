// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Extended Hamming (72,64) SECDED syndrome encoder / decoder.
//
// Same principle as the Hamming code of ECC_test1: the syndrome of a single
// error is the position of the bit in error. Bit j of the 72-bit word
// (j = 0..71) has parity-check column
//
//     h_col(j) = {1'b1, j[6:0]}
//
// so syndrome bits 6..0 are the binary position (Hamming (127,120) shortened
// to positions 0..71) and syndrome bit 7 is the overall parity of the word.
// Position 0 is the overall-parity bit (column 8'h80).
//
// Used as a syndrome secure sketch for the PUF (no data encoding):
//   enrollment:     syndrome = H * word, stored as helper data
//   reconstruction: s = H * word' ^ helper = H * error
//     s == 0                          -> no error
//     s == h_col(j) for some j        -> single error at j, corrected
//     otherwise (bit 7 clear and s != 0, or position > 71)
//                                     -> uncorrectable, detected
// Every 2-bit error has bit 7 clear and s != 0, so it is detected. Three or
// more errors can be miscorrected; the PUF's key-check value catches that.
//
// Purely combinational. Bit-exact with model/secded.py.
module secded72 (
    input  wire [71:0] word,
    input  wire [7:0]  helper,        // enrollment syndrome; 0 = plain check
    output wire [7:0]  syndrome,      // H * word
    output wire [71:0] corrected,     // word with a single error flipped
    output wire        single_error,  // one bit was corrected
    output wire        uncorrectable  // error detected, word left unchanged
);

    // Column j of the parity-check matrix H.
    function [7:0] h_col(input integer j);
        h_col = {1'b1, j[6:0]};
    endfunction

    reg [7:0] syn;
    integer i;
    always @* begin
        syn = 8'd0;
        for (i = 0; i < 72; i = i + 1)
            if (word[i])
                syn = syn ^ h_col(i);
    end
    assign syndrome = syn;

    wire [7:0] s = syn ^ helper;

    reg [71:0] flip;
    integer k;
    always @* begin
        for (k = 0; k < 72; k = k + 1)
            flip[k] = (s == h_col(k));
    end

    assign single_error  = |flip;
    assign uncorrectable = (s != 8'd0) && !single_error;
    assign corrected     = word ^ flip;

endmodule

`default_nettype wire
