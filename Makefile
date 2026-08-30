.PHONY: build check container-smoke evidence-container evidence-evalplus evidence-evalplus-m0 evidence-gateway evidence-kubernetes evidence-synthetic evalplus-m0 evalplus-smoke format gateway-smoke kubernetes-smoke lint schemas smoke test typecheck

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

evalplus-m0:
	EVALPLUS_MAX_MEMORY_BYTES=-1 $(PYTHON) -m verirun evalplus-m0 --output .verirun/evidence/m0/evalplus

gateway-smoke:
	$(PYTHON) -m verirun gateway-smoke --output .verirun/evidence/v0.2/gateway-smoke

container-smoke:
	test -n "$(VERIRUN_CONTAINER_IMAGE)"
	$(PYTHON) -m verirun container-smoke --image "$(VERIRUN_CONTAINER_IMAGE)" --output .verirun/evidence/v0.3/container-smoke

kubernetes-smoke:
	test -n "$(VERIRUN_CONTAINER_IMAGE)"
	test -n "$(VERIRUN_KUBERNETES_CONTEXT)"
	test -n "$(VERIRUN_KUBERNETES_NAMESPACE)"
	test -n "$(VERIRUN_KUBERNETES_RUNTIME_CLASS)"
	$(PYTHON) -m verirun kubernetes-smoke --image "$(VERIRUN_CONTAINER_IMAGE)" --kubernetes-context "$(VERIRUN_KUBERNETES_CONTEXT)" --kubernetes-namespace "$(VERIRUN_KUBERNETES_NAMESPACE)" --kubernetes-runtime-class "$(VERIRUN_KUBERNETES_RUNTIME_CLASS)" --output .verirun/evidence/v0.3/kubernetes-smoke

evidence-synthetic:
	$(PYTHON) -m verirun smoke --output evidence/v0.1/synthetic

evidence-evalplus:
	$(PYTHON) -m verirun evalplus-smoke --output evidence/v0.1/evalplus

evidence-evalplus-m0:
	EVALPLUS_MAX_MEMORY_BYTES=-1 $(PYTHON) -m verirun evalplus-m0 --output evidence/m0/evalplus

evidence-gateway:
	$(PYTHON) -m verirun gateway-smoke --output evidence/v0.2/gateway-smoke

evidence-container:
	test -n "$(VERIRUN_CONTAINER_IMAGE)"
	$(PYTHON) -m verirun container-smoke --image "$(VERIRUN_CONTAINER_IMAGE)" --output evidence/v0.3/container-smoke

evidence-kubernetes:
	test -n "$(VERIRUN_CONTAINER_IMAGE)"
	test -n "$(VERIRUN_KUBERNETES_CONTEXT)"
	test -n "$(VERIRUN_KUBERNETES_NAMESPACE)"
	test -n "$(VERIRUN_KUBERNETES_RUNTIME_CLASS)"
	$(PYTHON) -m verirun kubernetes-smoke --image "$(VERIRUN_CONTAINER_IMAGE)" --kubernetes-context "$(VERIRUN_KUBERNETES_CONTEXT)" --kubernetes-namespace "$(VERIRUN_KUBERNETES_NAMESPACE)" --kubernetes-runtime-class "$(VERIRUN_KUBERNETES_RUNTIME_CLASS)" --output evidence/v0.3/kubernetes-smoke

check: lint typecheck schemas test build smoke
