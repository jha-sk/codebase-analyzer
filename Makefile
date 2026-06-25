# Makefile for claude-codebase-analyzer
# Note: on Windows, run these via Git Bash / make, or run the underlying
# commands directly. Replace `python` with `.venv/Scripts/python` if needed.

PYTHON ?= python

.PHONY: install test lint typecheck clean build publish

install:
	$(PYTHON) -m pip install -e ".[dev,analysis]"

test:
	$(PYTHON) -m pytest -xvs --cov=src --cov-report=term-missing

lint:
	$(PYTHON) -m ruff check src tests && $(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff format src tests && $(PYTHON) -m ruff check --fix src tests

typecheck:
	$(PYTHON) -m mypy src

clean:
	rm -rf build/ dist/ .cache/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +

build:
	$(PYTHON) -m build

publish:
	$(PYTHON) -m twine upload dist/*
