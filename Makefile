PYTHON ?= python3
VENV ?= .venv
REQUIREMENTS ?= requirements-dev.txt

.PHONY: deps lint typecheck format test check smoke

deps:
	"$(PYTHON)" -m venv "$(VENV)"
	"$(VENV)/bin/python" -m pip install -r "$(REQUIREMENTS)"

lint:
	"$(VENV)/bin/ruff" check custom_components tests scripts
	"$(VENV)/bin/ruff" format --check custom_components tests scripts

typecheck:
	"$(VENV)/bin/pyright" --pythonpath "$(VENV)/bin/python" --warnings

# Formatting is an explicit operation, not mixed into dependency installation.
format:
	"$(VENV)/bin/ruff" format custom_components tests scripts

test:
	"$(VENV)/bin/python" -m pytest

check: lint typecheck test

# Install requirements.smoke.txt in a separate environment before this target.
smoke:
	"$(VENV)/bin/python" scripts/ha_runtime_smoke.py --expected-ha 2026.11.1
