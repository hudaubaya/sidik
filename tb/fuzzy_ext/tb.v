// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none
`timescale 1ns/1ps

// Testbench for rtl/fuzzy_ext.v. The measurement port is answered from an
// oracle memory filled by the cocotb test ($readmemh on `load`): table
// `table_sel`, one stream of N_SLOT races per pair, consumed in order
// (used[pair] counts the races taken since `clear_used`). The same streams
// feed model/ro_puf.py, so RTL and model see identical races.
module tb;
    localparam integer N_PAIRS = 512;
    localparam integer N_SLOT  = 16;
    localparam integer N_TABLE = 8;

    reg clk = 1'b0;
    always #10 clk = ~clk;   // 50 MHz

    reg          rst = 1'b1;
    reg          cmd_enroll = 1'b0, cmd_recon = 1'b0, cmd_abort = 1'b0;
    reg          enroll_first = 1'b0, enroll_last = 1'b0;
    reg  [15:0]  tau = 16'd0;
    reg  [511:0] helper_mask_in = 512'd0;
    reg  [23:0]  helper_syn_in = 24'd0;
    reg          key_ack = 1'b0, key_good = 1'b0;

    wire [511:0] helper_mask;
    wire [23:0]  helper_syn;
    wire         meas_req;
    wire [8:0]   meas_pair;
    reg          meas_ack = 1'b0;
    reg  signed [15:0] meas_delta = 16'sd0;
    wire         key_valid;
    wire [215:0] key;
    wire         busy, done, fail, kcv_caught;
    wire [2:0]   attempts;
    wire [9:0]   n_pass;

    fuzzy_ext u_fe (
        .clk(clk), .rst(rst),
        .cmd_enroll(cmd_enroll), .cmd_recon(cmd_recon), .cmd_abort(cmd_abort),
        .enroll_first(enroll_first), .enroll_last(enroll_last), .tau(tau),
        .helper_mask_in(helper_mask_in), .helper_syn_in(helper_syn_in),
        .helper_mask(helper_mask), .helper_syn(helper_syn),
        .meas_req(meas_req), .meas_pair(meas_pair), .meas_ack(meas_ack),
        .meas_delta(meas_delta),
        .key_valid(key_valid), .key(key), .key_ack(key_ack), .key_good(key_good),
        .busy(busy), .done(done), .fail(fail), .attempts(attempts),
        .kcv_caught(kcv_caught), .n_pass(n_pass));

    // --- race oracle ----------------------------------------------------------
    reg signed [15:0] oracle [0:N_TABLE*N_PAIRS*N_SLOT-1];
    reg [4:0]   used [0:N_PAIRS-1];
    reg [2:0]   table_sel = 3'd0;
    reg         load = 1'b0, clear_used = 1'b0;
    reg         overrun = 1'b0;
    reg [31:0]  n_races = 32'd0;
    reg [8*1024-1:0] path;
    integer i;

    always @(posedge load) begin
        if (!$value$plusargs("ORACLE=%s", path))
            $fatal(1, "+ORACLE=<file> missing");
        $readmemh(path, oracle);
    end

    always @(posedge clk) begin
        meas_ack <= 1'b0;
        if (clear_used) begin
            for (i = 0; i < N_PAIRS; i = i + 1)
                used[i] = 5'd0;
            n_races <= 32'd0;
        end else if (meas_req && !meas_ack) begin
            if (used[meas_pair] == N_SLOT)
                overrun <= 1'b1;
            meas_delta <= oracle[(table_sel * N_PAIRS + meas_pair) * N_SLOT + used[meas_pair]];
            used[meas_pair] <= used[meas_pair] + 1'b1;
            n_races <= n_races + 1'b1;
            meas_ack <= 1'b1;
        end
    end

endmodule

`default_nettype wire
