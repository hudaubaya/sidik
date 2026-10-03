# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for the Shaman SHA-256 core.
# Run from the repo root with `make test-shaman`, or here with `make`.

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

VERILOG_SOURCES += $(ROOT)/tb/shaman/tb.v
VERILOG_SOURCES += $(ROOT)/rtl/third_party/shaman/tt_um_psychogenic_shaman.v

TOPLEVEL = tb
MODULE = test_shaman

export PYTHONPATH := $(ROOT)/model:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
