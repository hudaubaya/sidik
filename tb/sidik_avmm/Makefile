# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for rtl/sidik_avmm.v.
# Run from the repo root with `make test-sidik_avmm` (both builds), or here:
#   make                 release build
#   make BUILD=char      characterization build (-DCHAR_BUILD)
# AVMM_RTL selects the RTL file (mutants.py points it at mutated copies).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
BUILD ?= release
RO_SEED ?= 20261004

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
AVMM_RTL ?= $(ROOT)/rtl/sidik_avmm.v

VERILOG_SOURCES += $(ROOT)/rtl/ro_cell.v $(ROOT)/rtl/ro_array.v $(ROOT)/rtl/puf_meas.v
VERILOG_SOURCES += $(ROOT)/rtl/ropuf/ropuf_core.v $(ROOT)/rtl/secded72.v $(ROOT)/rtl/fuzzy_ext.v
VERILOG_SOURCES += $(ROOT)/rtl/third_party/shaman/tt_um_psychogenic_shaman.v
VERILOG_SOURCES += $(ROOT)/rtl/sidik_crypto.v $(AVMM_RTL) $(ROOT)/tb/sidik_avmm/tb.v
COMPILE_ARGS += -g2012 -DSIM
ifeq ($(BUILD),char)
COMPILE_ARGS += -DCHAR_BUILD
SIM_BUILD ?= sim_build_char
endif
PLUSARGS += +RO_SEED=$(RO_SEED) +BUILD=$(BUILD)

TOPLEVEL = tb
MODULE = test_sidik_avmm

export PYTHONPATH := $(ROOT)/model:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
