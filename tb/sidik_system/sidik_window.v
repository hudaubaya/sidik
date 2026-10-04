// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Simulation model of the SIDIK window of fpga/release: the two instances
// sidik_a (larik A) and sidik_b (larik B) behind an address decoder
// equivalent to the Platform Designer interconnect of release_sys.tcl /
// add_to_ghrd.tcl, as seen by one 32-bit master (JTAG to Avalon master or
// the lightweight HPS bridge, relative to the window base):
//
//   byte address [BASE_A, BASE_A + 0x100)  -> sidik_a, word = address[7:2]
//   byte address [BASE_B, BASE_B + 0x100)  -> sidik_b, word = address[7:2]
//   anything else                          -> no slave: write ignored, read 0
//
// Reads return readdata with readdatavalid one cycle after the read, as the
// slaves (fixed read latency 1). Both instances share clk, rst and tamper_n
// (KEY0). Not synthesized: Platform Designer generates the real interconnect.
module sidik_window #(
    parameter integer LOG2N     = 14,
    parameter integer N_ENROLL  = 16,
    parameter [31:0]  MEAS_TIMEOUT = 32'd1048576,
    parameter [31:0]  BASE_A    = 32'h000,
    parameter [31:0]  BASE_B    = 32'h100,
    parameter integer RO_BASE_A = 0,      // SIM: behavioural ROs of A ...
    parameter integer RO_BASE_B = 1024    // ... and of B, i.e. two chips
) (
    input  wire        clk,
    input  wire        rst,
    input  wire        tamper_n,
    input  wire [31:0] address,          // byte address
    input  wire        write,
    input  wire [31:0] writedata,
    input  wire        read,
    output wire [31:0] readdata,
    output reg         readdatavalid
);
    wire hit_a = address >= BASE_A && address - BASE_A < 32'h100;
    wire hit_b = address >= BASE_B && address - BASE_B < 32'h100;
    wire [5:0] word = address[7:2];

    wire [31:0] rd_a, rd_b;
    reg  [1:0]  rsel;    // slave of the pending read: {b, a}

    always @(posedge clk) begin
        if (rst) begin
            rsel          <= 2'b00;
            readdatavalid <= 1'b0;
        end else begin
            rsel          <= read ? {hit_b, hit_a} : 2'b00;
            readdatavalid <= read;
        end
    end
    assign readdata = rsel[0] ? rd_a : rsel[1] ? rd_b : 32'd0;

    sidik_avmm #(.LOG2N(LOG2N), .N_ENROLL(N_ENROLL), .MEAS_TIMEOUT(MEAS_TIMEOUT),
                 .RO_INDEX_BASE(RO_BASE_A)) sidik_a (
        .clk(clk), .rst(rst), .tamper_n(tamper_n),
        .avs_address(word), .avs_write(write && hit_a), .avs_writedata(writedata),
        .avs_read(read && hit_a), .avs_readdata(rd_a));

    sidik_avmm #(.LOG2N(LOG2N), .N_ENROLL(N_ENROLL), .MEAS_TIMEOUT(MEAS_TIMEOUT),
                 .RO_INDEX_BASE(RO_BASE_B)) sidik_b (
        .clk(clk), .rst(rst), .tamper_n(tamper_n),
        .avs_address(word), .avs_write(write && hit_b), .avs_writedata(writedata),
        .avs_read(read && hit_b), .avs_readdata(rd_b));
endmodule

`default_nettype wire
