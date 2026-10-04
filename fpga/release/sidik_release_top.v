// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none

// DE10-Nano top level of the stand-alone SIDIK release build (no HPS).
//
// release_sys (release_sys.tcl): JTAG to Avalon master -> sidik_a (larik A,
// 0x000) and sidik_b (larik B, 0x100). sw/verifier.py --jtag reaches them
// through fpga/release/syscon/sidik_bridge.tcl.
//
// KEY[0] is the tamper input of both instances (active low, pressed = 0):
// pressing it erases K and every buffer of A and B within 3 clock cycles
// and sets TAMPERED until reset. It goes to the instances unsynchronized;
// each synchronizes it with its own 2-flop synchronizer.
// KEY[1] resets the system (and clears TAMPERED).
// LED[0] heartbeat, LED[1] out of reset, LED[2] KEY[0] pressed.
module sidik_release_top (
    input  wire       FPGA_CLK1_50,
    input  wire [1:0] KEY,
    output wire [7:0] LED
);

    wire clk = FPGA_CLK1_50;

    // Reset: asynchronous assert, synchronous release.
    reg [1:0] rst_n_sync;
    always @(posedge clk or negedge KEY[1]) begin
        if (!KEY[1])
            rst_n_sync <= 2'b00;
        else
            rst_n_sync <= {rst_n_sync[0], 1'b1};
    end

    release_sys u_sys (
        .clk_clk                 (clk),
        .reset_reset_n           (rst_n_sync[1]),
        .sidik_a_tamper_tamper_n (KEY[0]),
        .sidik_b_tamper_tamper_n (KEY[0])
    );

    reg [24:0] heartbeat;
    always @(posedge clk)
        heartbeat <= heartbeat + 1'b1;

    reg [1:0] key0_sync;   // LED only
    always @(posedge clk)
        key0_sync <= {key0_sync[0], ~KEY[0]};

    assign LED = {5'b0, key0_sync[1], rst_n_sync[1], heartbeat[24]};

endmodule

`default_nettype wire
