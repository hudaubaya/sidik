# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# SIDIK top-level Makefile.
#   make install   install Python deps (cocotb, numpy, matplotlib)
#   make test      run every test (model unit tests + RTL cocotb tests)
#   make clean     remove simulation outputs

PYTHON ?= python3
SIM    ?= icarus

RTL_TESTS := shaman

.PHONY: all install test test-model test-rtl $(addprefix test-,$(RTL_TESTS)) check-tools clean

all: test

install:
	$(PYTHON) -m pip install -r requirements.txt

test: test-model test-rtl

test-model:
	cd model && $(PYTHON) -m unittest discover -v -p 'test_*.py'

test-rtl: $(addprefix test-,$(RTL_TESTS))

$(addprefix test-,$(RTL_TESTS)): test-%: check-tools
	$(MAKE) -C tb/$* SIM=$(SIM)
	@! grep -q '<failure' tb/$*/results.xml || { echo "FAIL: tb/$*"; exit 1; }

check-tools:
	@command -v iverilog >/dev/null || { echo "iverilog not found (apt install iverilog)"; exit 1; }
	@command -v cocotb-config >/dev/null || { echo "cocotb not found (make install)"; exit 1; }

clean:
	for t in $(RTL_TESTS); do \
		$(MAKE) -C tb/$$t clean; \
		rm -f tb/$$t/results.xml tb/$$t/tb.vcd; \
	done
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
