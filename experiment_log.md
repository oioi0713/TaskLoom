# TaskLoom Experiment Log

This log records the functionality, validation results, and known limitations of each project version.

## v0.1.0 - Stage 0: Project initialization

Date: 2026-11-02

### Added

- Initialized the Git repository with `main` as the default branch.
- Added the `app` application package and `tests` test package.
- Added a Python 3.11/3.12 project definition in `pyproject.toml`.
- Declared the initial LangGraph, LangChain Core, Pydantic, environment, and test dependencies.
- Added validated configuration loading from environment variables and `.env`.
- Added centralized text and JSON console logging.
- Added the `python -m app.main` application entry point.
- Added editable package installation and the `taskloom` console command.
- Added unit tests for configuration, logging, and application startup.
- Added local environment templates, Git ignore rules, setup instructions, and basic usage documentation.

### Validation

- Environment: Python 3.11.18 in the `TaskLoom` Conda environment.
- Application startup: `python -m app.main` succeeded.
- Test suite: 6 tests passed.
- GitHub synchronization: local `main` matched `origin/main` after the initial push.

### Known limitations

- This version only provides the runnable project skeleton from development stage 0.
- Core data contracts, registries, Plan Guard, graph workflow, and real agents are not implemented yet.
- The shared Conda environment contains unrelated packages with missing optional dependencies; these do not affect the stage 0 application or tests.
