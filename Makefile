# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# SIDIK top-level Makefile.
#   make install   install Python deps (cocotb, numpy, matplotlib)
#   make test      run every test (model, characterization and RTL tests)
#   make synth-check  generic yosys synthesis of rtl/ropuf (needs yosys)
#   make puf-model rerun the RO-PUF Monte Carlo (docs/puf-model/)
#   make clean     remove simulation outputs

PYTHON ?= python3
SIM    ?= icarus

RTL_TESTS := shaman ropuf

.PHONY: all install test test-model test-char test-rtl $(addprefix test-,$(RTL_TESTS)) check-tools synth-check puf-model clean

all: test

install:
	$(PYTHON) -m pip install -r requirements.txt

test: test-model test-char test-rtl

test-model:
	cd model && $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-char:
	cd fpga/char && PYTHONPATH=../../model $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-rtl: $(addprefix test-,$(RTL_TESTS))

$(addprefix test-,$(RTL_TESTS)): test-%: check-tools
	$(MAKE) -C tb/$* SIM=$(SIM)
	@! grep -q '<failure' tb/$*/results.xml || { echo "FAIL: tb/$*"; exit 1; }

synth-check:
	@command -v yosys >/dev/null || { echo "yosys not found (apt install yosys)"; exit 1; }
	$(PYTHON) rtl/ropuf/synth_check.py

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
