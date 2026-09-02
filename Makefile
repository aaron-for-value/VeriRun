.PHONY: build check container-smoke control-plane-smoke evidence-container evidence-control-plane evidence-evalplus evidence-evalplus-m0 evidence-gateway evidence-kubernetes evidence-synthetic evalplus-m0 evalplus-smoke format gateway-smoke kubernetes-smoke lint schemas smoke test test-unit typecheck

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
	@test -n "$(VERIRUN_TEST_POSTGRES_DSN)" || (echo "VERIRUN_TEST_POSTGRES_DSN is required for the full coverage gate"; exit 1)
	@test -n "$(VERIRUN_TEST_S3_ENDPOINT)" || (echo "VERIRUN_TEST_S3_ENDPOINT is required for the full coverage gate"; exit 1)
	@test -n "$(VERIRUN_TEST_S3_ACCESS_KEY)" || (echo "VERIRUN_TEST_S3_ACCESS_KEY is required for the full coverage gate"; exit 1)
	@test -n "$(VERIRUN_TEST_S3_SECRET_KEY)" || (echo "VERIRUN_TEST_S3_SECRET_KEY is required for the full coverage gate"; exit 1)
	$(PYTHON) -m pytest --cov=verirun --cov-report=term-missing

test-unit:
	$(PYTHON) -m pytest -q

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

control-plane-smoke:
	@test -n "$(VERIRUN_POSTGRES_DSN)"
	@test -n "$(VERIRUN_S3_ENDPOINT)"
	@test -n "$(VERIRUN_S3_ACCESS_KEY)"
	@test -n "$(VERIRUN_S3_SECRET_KEY)"
	@test -n "$(VERIRUN_S3_SERVER_IDENTITY)"
	$(PYTHON) -m verirun control smoke --s3-endpoint "$(VERIRUN_S3_ENDPOINT)" --s3-server-identity "$(VERIRUN_S3_SERVER_IDENTITY)" --no-s3-secure --output .verirun/evidence/v0.4/control-plane

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

evidence-control-plane:
	@test -n "$(VERIRUN_POSTGRES_DSN)"
	@test -n "$(VERIRUN_S3_ENDPOINT)"
	@test -n "$(VERIRUN_S3_ACCESS_KEY)"
	@test -n "$(VERIRUN_S3_SECRET_KEY)"
	@test -n "$(VERIRUN_S3_SERVER_IDENTITY)"
	$(PYTHON) -m verirun control smoke --s3-endpoint "$(VERIRUN_S3_ENDPOINT)" --s3-server-identity "$(VERIRUN_S3_SERVER_IDENTITY)" --no-s3-secure --output evidence/v0.4/control-plane

check: lint typecheck schemas test build smoke
