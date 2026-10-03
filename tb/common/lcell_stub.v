// SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
// SPDX-License-Identifier: GPL-3.0-or-later

// Stand-in for the Intel `lcell` primitive, only to compile-check the
// CYCLONEV path of rtl/ro_cell.v outside Quartus. Quartus provides the real
// primitive.
`timescale 1ns/1ps
module lcell (input wire in, output wire out);
    assign out = in;
endmodule
