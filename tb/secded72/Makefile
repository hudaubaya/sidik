# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for rtl/secded72.v against model/secded.py.
# Run from the repo root with `make test-secded72`, or here with `make`.
# SECDED_RTL selects the RTL file (mutants.py points it at mutated copies).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
SECDED_RTL ?= $(ROOT)/rtl/secded72.v

VERILOG_SOURCES += $(SECDED_RTL)

TOPLEVEL = secded72
MODULE = test_secded72

export PYTHONPATH := $(ROOT)/model:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
