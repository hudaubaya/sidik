# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench of the combined fpga/release system (two sidik_avmm
# behind the address decoder), driven by sw/verifier.py and sw/sidik_verifier.c.
# Run from the repo root with `make test-sidik_system`, or here with `make`.

SIM ?= icarus
TOPLEVEL_LANG ?= verilog
RO_SEED ?= 20261004

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

VERILOG_SOURCES += $(ROOT)/rtl/ro_cell.v $(ROOT)/rtl/ro_array.v $(ROOT)/rtl/puf_meas.v
VERILOG_SOURCES += $(ROOT)/rtl/ropuf/ropuf_core.v $(ROOT)/rtl/secded72.v $(ROOT)/rtl/fuzzy_ext.v
VERILOG_SOURCES += $(ROOT)/rtl/third_party/shaman/tt_um_psychogenic_shaman.v
VERILOG_SOURCES += $(ROOT)/rtl/sidik_crypto.v $(ROOT)/rtl/sidik_avmm.v
VERILOG_SOURCES += $(ROOT)/tb/sidik_system/sidik_window.v $(ROOT)/tb/sidik_system/tb.v
COMPILE_ARGS += -g2012 -DSIM
PLUSARGS += +RO_SEED=$(RO_SEED)

TOPLEVEL = tb
MODULE = test_sidik_system

export PYTHONPATH := $(ROOT)/model:$(ROOT)/sw:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
