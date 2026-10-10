[SmartGate](../../../README.md) / [Documentation](../README.md) / Local development

[English](README.md) · [Русский](../../ru/development/README.md)

# Local development

This guide covers development and local diagnostics. The controller itself runs in GitHub Actions without local Python. However, reading a detailed encrypted plan still requires Python on a trusted device — see [how to decrypt a report](../operations/privacy.md#reading-a-detailed-report). Setup starts with the [quick start](../../../README.md#quick-start).

Python >=3.12,<3.14 is required. Run all commands from the checkout root.

## On this page

- [Installation](#installation)
- [Checks](#checks)
- [Configuration](#configuration)
- [Updating lock files](#updating-lock-files)

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
```

For Linux/macOS, use .venv/bin/python.

## Checks

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not live" -q
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m ruff format --check src tests tools
.\.venv\Scripts\python.exe tools/check_docs.py
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m build --no-isolation
```

To validate configuration and preview a test plan without keys or network calls:

```powershell
.\.venv\Scripts\python.exe -m smartgate validate --config config/smartgate.example.toml
.\.venv\Scripts\python.exe -m smartgate preview --config config/smartgate.example.toml --offline --fixtures tests/fixtures
```

Offline preview uses test sources and simulated TLS results.

Offline tests prohibit external socket connections and require no secrets. Live tests have a separate opt-in and use only an empty isolated profile.

## Configuration

Example: config/smartgate.example.toml. Catalog: config/services.toml. Keep real configuration separately in config/smartgate.toml; it is excluded from Git.

Never save API keys in TOML, documents, test fixtures or Git. Production credentials are supplied through env variables.

## Updating lock files

```powershell
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --strip-extras -o requirements.lock pyproject.toml
.\.venv\Scripts\python.exe -m piptools compile --generate-hashes --allow-unsafe --strip-extras --extra dev -o requirements-dev.lock pyproject.toml
```

After changing dependencies, verify installation with --require-hashes, the full offline suite, types and build.

---

[All documentation](../README.md) · [Home](../../../README.md) · [Actions setup](../operations/actions.md) · [Troubleshooting](../operations/runbook.md)
