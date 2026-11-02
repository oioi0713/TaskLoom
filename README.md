# TaskLoom

TaskLoom is a constrained multi-agent task orchestration project built around a fixed LangGraph workflow and short-lived dynamic workers.

The repository currently implements **development plan stage 0**: a Python package skeleton, validated environment configuration, centralized logging, dependency metadata, and tests.

## Requirements

- Python 3.11 or 3.12

## Setup

Using the existing Conda environment:

```powershell
conda activate TaskLoom
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Or create a repository-local virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

The local `.env` contains non-secret development defaults and is ignored by Git. `.env.example` is the committed configuration template.

## Run

```powershell
python -m app.main
```

## Test

```powershell
pytest
```

See [the development plan](docs/taskloom-development-plan.md) for the complete architecture and roadmap.
