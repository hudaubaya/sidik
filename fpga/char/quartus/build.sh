#!/bin/sh
# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Build the DE10-Nano RO-PUF characterization bitstream.
#   ./build.sh            generate char_sys, compile -> output_files/sidik_char.sof
#   ./build.sh program    also program the FPGA over the USB-Blaster II
# Needs Quartus Prime (Lite or Standard) with Cyclone V support on PATH
# (quartus_sh, qsys-script, qsys-generate). See docs/char_howto.md.
set -eu
cd "$(dirname "$0")"

for t in quartus_sh qsys-script qsys-generate; do
    command -v "$t" >/dev/null || { echo "$t not found: add Quartus bin dirs to PATH" >&2; exit 1; }
done

qsys-script --script=char_sys.tcl --search-path="$PWD,\$"
qsys-generate char_sys.qsys --synthesis=VERILOG --part=5CSEBA6U23I7 \
    --search-path="$PWD,\$" --output-directory=char_sys
quartus_sh --flow compile sidik_char

# Expect one combinational-loop warning per RO (1024) if Quartus lists them
# individually. Far fewer means rings were merged or removed: check before
# measuring (docs/char_howto.md, step 2).
loops=$(cat output_files/*.rpt 2>/dev/null | grep -c "Found combinational loop" || true)
echo "combinational-loop warnings: ${loops:-0} (expect 1024)"

sha256sum output_files/sidik_char.sof | tee output_files/sidik_char.sof.sha256

if [ "${1:-}" = program ]; then
    # On the DE10-Nano JTAG chain the HPS is device 1 and the FPGA device 2.
    quartus_pgm -m jtag -o "p;output_files/sidik_char.sof@2"
fi
