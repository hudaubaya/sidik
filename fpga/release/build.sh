#!/bin/sh
# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Build the stand-alone DE10-Nano SIDIK release bitstream (two instances,
# JTAG only). The HPS (GHRD) variant: README.md.
#   ./build.sh            generate release_sys, compile -> output_files/sidik_release.sof
#   ./build.sh program    also program the FPGA over the USB-Blaster II
# Needs Quartus Prime (Lite or Standard) with Cyclone V support on PATH.
set -eu
cd "$(dirname "$0")"

for t in quartus_sh qsys-script qsys-generate; do
    command -v "$t" >/dev/null || { echo "$t not found: add Quartus bin dirs to PATH" >&2; exit 1; }
done

qsys-script --script=release_sys.tcl --search-path="$PWD,\$"
qsys-generate release_sys.qsys --synthesis=VERILOG --part=5CSEBA6U23I7 \
    --search-path="$PWD,\$" --output-directory=release_sys
quartus_sh -t sidik_assignments.tcl sidik_release
quartus_sh --flow compile sidik_release

# Two arrays of 1024 rings: expect 2048 combinational-loop warnings if
# Quartus lists them individually. Far fewer means rings were merged.
loops=$(cat output_files/*.rpt 2>/dev/null | grep -c "Found combinational loop" || true)
echo "combinational-loop warnings: ${loops:-0} (expect 2048)"

sha256sum output_files/sidik_release.sof | tee output_files/sidik_release.sof.sha256

if [ "${1:-}" = program ]; then
    # On the DE10-Nano JTAG chain the HPS is device 1 and the FPGA device 2.
    quartus_pgm -m jtag -o "p;output_files/sidik_release.sof@2"
fi
