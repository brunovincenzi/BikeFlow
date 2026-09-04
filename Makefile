PYTHON ?= .venv/bin/python

.PHONY: install lint format test test-integration compose-up compose-down k8s-validate

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check backend simulator tests
	$(PYTHON) -m ruff format --check backend simulator tests

format:
	$(PYTHON) -m ruff check --fix backend simulator tests
	$(PYTHON) -m ruff format backend simulator tests

test:
	$(PYTHON) -m pytest -m "not integration"

test-integration:
	$(PYTHON) -m pytest -m integration

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down

k8s-validate:
	kubectl kustomize k8s >/dev/null
