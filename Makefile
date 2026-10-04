# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# SIDIK top-level Makefile.
#   make install   install Python deps (cocotb, numpy, matplotlib)
#   make test      run every test (model, characterization, release, sw and
#                  RTL tests)
#   make synth-check  yosys synthesis of the RO-PUF, secded72, fuzzy_ext and
#                     sidik_crypto;
#                     compile-check the CYCLONEV path of rtl/ro_cell.v
#   make test-mutants mutation checks of the secded72, fuzzy_ext,
#                     sidik_crypto and sidik_avmm testbenches
#   make synth-check-full  yosys synthesis of the whole sidik_avmm (~3 min,
#                     not in CI)
#   make puf-model rerun the RO-PUF Monte Carlo (docs/puf-model/)
#   make clean     remove simulation outputs

PYTHON ?= python3
SIM    ?= icarus

RTL_TESTS := shaman ropuf puf_meas secded72 fuzzy_ext sidik_crypto

.PHONY: all install test test-model test-char test-release test-sw test-rtl test-sidik_avmm test-sidik_system synth-check-full $(addprefix test-,$(RTL_TESTS)) check-tools test-mutants synth-check puf-model clean

all: test

install:
	$(PYTHON) -m pip install -r requirements.txt

test: test-model test-char test-release test-sw test-rtl test-mutants

test-model:
	cd model && $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-char:
	cd fpga/char && PYTHONPATH=../../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-release:
	cd fpga/release && PYTHONPATH=../../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-sw:
	cd sw && PYTHONPATH=../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-rtl: $(addprefix test-,$(RTL_TESTS)) test-sidik_avmm test-sidik_system

# sidik_avmm: all tests on the release build, the address scan on CHAR_BUILD.
test-sidik_avmm: check-tools
	$(MAKE) -C tb/sidik_avmm SIM=$(SIM)
	@! grep -q '<failure' tb/sidik_avmm/results.xml || { echo "FAIL: tb/sidik_avmm (release)"; exit 1; }
	$(MAKE) -C tb/sidik_avmm SIM=$(SIM) BUILD=char TESTCASE=test_flow_and_address_scan \
		COCOTB_RESULTS_FILE=results_char.xml
	@grep -q 'testcase' tb/sidik_avmm/results_char.xml && \
		! grep -q '<failure' tb/sidik_avmm/results_char.xml || { echo "FAIL: tb/sidik_avmm (CHAR_BUILD)"; exit 1; }

# Combined fpga/release system (two instances, address decoder) driven by
# sw/verifier.py and sw/sidik_verifier.c.
test-sidik_system: check-tools
	$(MAKE) -C tb/sidik_system SIM=$(SIM)
	@grep -q 'testcase' tb/sidik_system/results.xml && \
		! grep -q '<failure' tb/sidik_system/results.xml || { echo "FAIL: tb/sidik_system"; exit 1; }

$(addprefix test-,$(RTL_TESTS)): test-%: check-tools
	$(MAKE) -C tb/$* SIM=$(SIM)
	@! grep -q '<failure' tb/$*/results.xml || { echo "FAIL: tb/$*"; exit 1; }

test-mutants: check-tools
	$(PYTHON) tb/secded72/mutants.py
	$(PYTHON) tb/fuzzy_ext/mutants.py
	$(PYTHON) tb/sidik_crypto/mutants.py
	$(PYTHON) tb/sidik_avmm/mutants.py

synth-check:
	@command -v yosys >/dev/null || { echo "yosys not found (apt install yosys)"; exit 1; }
	$(PYTHON) rtl/ropuf/synth_check.py
	iverilog -g2012 -DCYCLONEV -o /dev/null -s ro_array tb/common/lcell_stub.v rtl/ro_cell.v rtl/ro_array.v
	@echo "OK, the CYCLONEV (lcell) path of rtl/ro_cell.v compiles"
	yosys -q -p "read_verilog rtl/secded72.v; synth -top secded72; check -assert"
	@echo "OK, rtl/secded72.v synthesizes cleanly"
	yosys -q -p "read_verilog rtl/secded72.v rtl/fuzzy_ext.v; synth -top fuzzy_ext; check -assert"
	@echo "OK, rtl/fuzzy_ext.v synthesizes cleanly"
	yosys -q -p "read_verilog rtl/third_party/shaman/tt_um_psychogenic_shaman.v rtl/sidik_crypto.v; synth -top sidik_crypto; check -assert"
	@echo "OK, rtl/sidik_crypto.v synthesizes cleanly"

AVMM_SOURCES := rtl/ro_cell.v rtl/ro_array.v rtl/puf_meas.v rtl/ropuf/ropuf_core.v \
	rtl/secded72.v rtl/fuzzy_ext.v rtl/third_party/shaman/tt_um_psychogenic_shaman.v \
	rtl/sidik_crypto.v rtl/sidik_avmm.v

synth-check-full:
	@command -v yosys >/dev/null || { echo "yosys not found (apt install yosys)"; exit 1; }
	yosys -q -p "read_verilog $(AVMM_SOURCES); synth -top sidik_avmm; check -assert"
	@echo "OK, rtl/sidik_avmm.v (release build) synthesizes cleanly"

puf-model:
	$(PYTHON) model/puf_montecarlo.py --out docs/puf-model

check-tools:
	@command -v iverilog >/dev/null || { echo "iverilog not found (apt install iverilog)"; exit 1; }
	@command -v cocotb-config >/dev/null || { echo "cocotb not found (make install)"; exit 1; }

clean:
	for t in $(RTL_TESTS) sidik_avmm sidik_system; do \
		$(MAKE) -C tb/$$t clean; \
		rm -f tb/$$t/results.xml tb/$$t/tb.vcd; \
	done
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
