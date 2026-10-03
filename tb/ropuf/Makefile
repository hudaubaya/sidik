# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for the RO-PUF core (behavioural ROs, -DSIM).
# Run from the repo root with `make test-ropuf`, or here with `make`.

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
RTL := $(ROOT)/rtl/ropuf

VERILOG_SOURCES += $(RTL)/ro_cell.v $(RTL)/race_counter.v
VERILOG_SOURCES += $(RTL)/ropuf_core.v $(RTL)/ropuf_avmm.v
COMPILE_ARGS += -DSIM

TOPLEVEL = ropuf_avmm
MODULE = test_ropuf

include $(shell cocotb-config --makefiles)/Makefile.sim
