# Contributing to mcp-metrics

Thanks for your interest in contributing! This document covers the workflow,
coding style, and conventions used in the project.

## Code of conduct

Be respectful and constructive. We're all here to ship good software.

## How to contribute

### 1. Fork & clone

```bash
git clone https://github.com/<your-fork>/metrics.git
cd metrics
pip install -e ".[dev]"
```

### 2. Create a branch

Use a descriptive branch name:

```bash
git checkout -b feature/<short-description>
git checkout -b fix/<issue-number>-<short-description>
```

### 3. Make your changes

- Keep changes focused — one logical change per PR.
- Add or update tests under `tests/` when behaviour changes.
- Update `README.md` / `docs/` if the public API changes.

### 4. Run checks locally before pushing

```bash
ruff check .
ruff format --check .
pytest tests/ -v
```

CI runs the same checks — failures there will block merge.

### 5. Commit

We follow [Conventional Commits](https://www.conventionalcommits.org/).

Format:

```
<type>(<scope>): <short summary>

<body (optional)>

<footer (optional)>
```

Common types:

- `feat:` — new feature
- `fix:` — bug fix
- `docs:` — documentation only
- `style:` — formatting, no code change
- `refactor:` — code change that neither fixes a bug nor adds a feature
- `test:` — adding or fixing tests
- `chore:` — tooling, build, deps

Examples:

```
feat(server): add git_integration table sync endpoint
fix(queries): handle NULL task_type in MQI calc
docs(readme): document METRICS_DB_* env vars
```

Commit messages must be in **English**.

### 6. Push & open a Pull Request

```bash
git push origin feature/<short-description>
```

Open a PR against `main`. Fill in the PR template:

- What does this PR do?
- How was it tested?
- Linked issues (if any).

A maintainer will review. Address review feedback with new commits
(squash locally if asked).

## Coding style

- **Python**: PEP 8, enforced by [ruff](https://docs.astral.sh/ruff/).
  - Line length: 100 (see `pyproject.toml`).
  - Target: Python 3.10+.
  - Run `ruff format .` before committing.
- **SQL**: lowercase keywords, explicit `IF NOT EXISTS` on `CREATE`,
  schema files live under `schema/`.
- **PHP tools** (`api*.php`): keep parameter parsing consistent with
  existing endpoints; prefer `?param=` query strings and JSON output.

## Tests

- Tests live in `tests/` and run with `pytest`.
- Tests that touch the database read connection from the
  `METRICS_DB_*` environment variables (see `tests/test_queries.py`).
- CI brings up a `postgres:15` service automatically; locally you can
  point at any reachable Postgres instance.

## Project layout

```
metrics_mcp/        # Python package (server, queries)
schema/             # SQL migrations applied in order
api*.php            # optional PHP HTTP tools for the dashboard
tests/              # pytest suite
docs/               # long-form documentation
```

## Reporting bugs

Open an issue using the **Bug report** template and include:

- Steps to reproduce
- Expected vs actual behaviour
- Environment (version, Python, PostgreSQL)
- Logs / stack traces

## Security issues

Please see `SECURITY.md` — do **not** open a public issue for security
vulnerabilities.

## Licence

By contributing, you agree that your contributions will be licensed under
the Apache-2.0 License (see `LICENSE`).
