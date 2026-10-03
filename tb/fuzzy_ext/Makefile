# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for rtl/fuzzy_ext.v against model/ro_puf.py.
# Run from the repo root with `make test-fuzzy_ext`, or here with `make`.
# FE_RTL selects the RTL file (mutants.py points it at mutated copies);
# FE_CHIPS sets the number of virtual chips (default 200).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
FE_RTL ?= $(ROOT)/rtl/fuzzy_ext.v
SIM_BUILD ?= sim_build

VERILOG_SOURCES += $(FE_RTL) $(ROOT)/rtl/secded72.v $(ROOT)/tb/fuzzy_ext/tb.v
COMPILE_ARGS += -g2012
PLUSARGS += +ORACLE=$(abspath $(SIM_BUILD))/oracle.hex

TOPLEVEL = tb
MODULE = test_fuzzy_ext

export PYTHONPATH := $(ROOT)/model:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
