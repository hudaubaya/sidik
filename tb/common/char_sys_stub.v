// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

`default_nettype none

// Compile-check stand-in for the Platform Designer system char_sys
// (fpga/char/quartus/char_sys.tcl). The real system adds the JTAG to Avalon
// master; this stub only connects the RO-PUF slave so the top level and the
// RTL file list can be compiled without Quartus.
module char_sys (
    input wire clk_clk,
    input wire reset_reset_n
);
    wire [31:0] readdata;

    ropuf_avmm ropuf_0 (
        .clk(clk_clk), .reset(~reset_reset_n),
        .avs_address(3'd0), .avs_write(1'b0), .avs_writedata(32'd0),
        .avs_read(1'b0), .avs_readdata(readdata));
endmodule

`default_nettype wire
