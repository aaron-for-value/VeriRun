.PHONY: build check evidence-evalplus evidence-gateway evidence-synthetic evalplus-smoke format gateway-smoke lint schemas smoke test typecheck

PYTHON := .venv/bin/python

format:
	$(PYTHON) -m ruff format .

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy src

schemas:
	$(PYTHON) scripts/export_schemas.py --check

test:
	$(PYTHON) -m pytest --cov=verirun --cov-report=term-missing

build:
	$(PYTHON) -m build

smoke:
	$(PYTHON) -m verirun smoke --output .verirun/evidence/v0.1/synthetic

evalplus-smoke:
	$(PYTHON) -m verirun evalplus-smoke --output .verirun/evidence/v0.1/evalplus

gateway-smoke:
	$(PYTHON) -m verirun gateway-smoke --output .verirun/evidence/v0.2/gateway-smoke

evidence-synthetic:
	$(PYTHON) -m verirun smoke --output evidence/v0.1/synthetic

evidence-evalplus:
	$(PYTHON) -m verirun evalplus-smoke --output evidence/v0.1/evalplus

evidence-gateway:
	$(PYTHON) -m verirun gateway-smoke --output evidence/v0.2/gateway-smoke

check: lint typecheck schemas test build smoke
