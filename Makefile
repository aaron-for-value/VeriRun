.PHONY: build check evidence-evalplus evidence-synthetic evalplus-smoke format lint schemas smoke test typecheck

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

evidence-synthetic:
	$(PYTHON) -m verirun smoke --output evidence/v0.1/synthetic

evidence-evalplus:
	$(PYTHON) -m verirun evalplus-smoke --output evidence/v0.1/evalplus

check: lint typecheck schemas test build smoke
