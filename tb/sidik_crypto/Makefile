# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# cocotb testbench for rtl/sidik_crypto.v against Python hashlib / hmac.
# Run from the repo root with `make test-sidik_crypto`, or here with `make`.
# CRYPTO_CASES sets the number of random keys / challenges (default 1000);
# CRYPTO_RTL selects the RTL file (mutants.py points it at mutated copies).

SIM ?= icarus
TOPLEVEL_LANG ?= verilog

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)

CRYPTO_RTL ?= $(ROOT)/rtl/sidik_crypto.v
VERILOG_SOURCES += $(CRYPTO_RTL)
VERILOG_SOURCES += $(ROOT)/rtl/third_party/shaman/tt_um_psychogenic_shaman.v
VERILOG_SOURCES += $(ROOT)/tb/sidik_crypto/tb.v

TOPLEVEL = tb
MODULE = test_sidik_crypto

export PYTHONPATH := $(ROOT)/model:$(PYTHONPATH)

include $(shell cocotb-config --makefiles)/Makefile.sim
