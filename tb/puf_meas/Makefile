# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for rtl/ro_array.v + rtl/puf_meas.v (behavioural ROs).
# Run from the repo root with `make test-puf_meas`, or here with `make`.
# RO_SEED selects the pseudo-random RO periods (RO_SEED=n make ...).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
RO_SEED ?= 20261003

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

VERILOG_SOURCES += $(ROOT)/rtl/ro_cell.v $(ROOT)/rtl/ro_array.v
VERILOG_SOURCES += $(ROOT)/rtl/puf_meas.v $(ROOT)/tb/puf_meas/tb.v
COMPILE_ARGS += -DSIM
PLUSARGS += +RO_SEED=$(RO_SEED)

TOPLEVEL = tb
MODULE = test_puf_meas

export PYTHONPATH := $(ROOT)/tb/common:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
