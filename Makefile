# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# SIDIK top-level Makefile.
#   make install   install Python deps (cocotb, numpy, matplotlib)
#   make test      run every test (model, characterization, sw and RTL tests)
#   make synth-check  yosys synthesis of the RO-PUF, secded72 and fuzzy_ext;
#                     compile-check the CYCLONEV path of rtl/ro_cell.v
#   make test-mutants mutation checks of the secded72 and fuzzy_ext testbenches
#   make puf-model rerun the RO-PUF Monte Carlo (docs/puf-model/)
#   make clean     remove simulation outputs

PYTHON ?= python3
SIM    ?= icarus

RTL_TESTS := shaman ropuf puf_meas secded72 fuzzy_ext

.PHONY: all install test test-model test-char test-sw test-rtl $(addprefix test-,$(RTL_TESTS)) check-tools test-mutants synth-check puf-model clean

all: test

install:
	$(PYTHON) -m pip install -r requirements.txt

test: test-model test-char test-sw test-rtl test-mutants

test-model:
	cd model && $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-char:
	cd fpga/char && PYTHONPATH=../../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-sw:
	cd sw && PYTHONPATH=../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-rtl: $(addprefix test-,$(RTL_TESTS))

$(addprefix test-,$(RTL_TESTS)): test-%: check-tools
	$(MAKE) -C tb/$* SIM=$(SIM)
	@! grep -q '<failure' tb/$*/results.xml || { echo "FAIL: tb/$*"; exit 1; }

test-mutants: check-tools
	$(PYTHON) tb/secded72/mutants.py
	$(PYTHON) tb/fuzzy_ext/mutants.py

synth-check:
	@command -v yosys >/dev/null || { echo "yosys not found (apt install yosys)"; exit 1; }
	$(PYTHON) rtl/ropuf/synth_check.py
	iverilog -g2012 -DCYCLONEV -o /dev/null -s ro_array tb/common/lcell_stub.v rtl/ro_cell.v rtl/ro_array.v
	@echo "OK, the CYCLONEV (lcell) path of rtl/ro_cell.v compiles"
	yosys -q -p "read_verilog rtl/secded72.v; synth -top secded72; check -assert"
	@echo "OK, rtl/secded72.v synthesizes cleanly"
	yosys -q -p "read_verilog rtl/secded72.v rtl/fuzzy_ext.v; synth -top fuzzy_ext; check -assert"
	@echo "OK, rtl/fuzzy_ext.v synthesizes cleanly"

puf-model:
	$(PYTHON) model/puf_montecarlo.py --out docs/puf-model

check-tools:
	@command -v iverilog >/dev/null || { echo "iverilog not found (apt install iverilog)"; exit 1; }
	@command -v cocotb-config >/dev/null || { echo "cocotb not found (make install)"; exit 1; }

clean:
	for t in $(RTL_TESTS); do \
		$(MAKE) -C tb/$$t clean; \
		rm -f tb/$$t/results.xml tb/$$t/tb.vcd; \
	done
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
