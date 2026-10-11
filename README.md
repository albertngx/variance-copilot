# Variance Copilot

Answers the question every finance team asks after month-end close: **why are actual results different from plan?** Numbers are computed deterministically from the ledger; the language model only explains them, and every run leaves a verifiable audit trail.

- [SPEC.md](SPEC.md) — normative specification
- [BACKLOG.md](BACKLOG.md) — delivery backlog
- [design-docs/](design-docs/) — how the parts work, with diagrams
- [docs/adr/](docs/adr/) — architecture decision records

## Quick start

Prerequisites: Docker, [`uv`](https://docs.astral.sh/uv/), `make`. `uv` installs Python 3.12 if needed.

```bash
cp .env.example .env
uv sync                 # create .venv and install dependencies from uv.lock
make up                 # PostgreSQL + pgvector, OpenTelemetry collector, Jaeger (http://localhost:16686)
make test               # unit, property, integration (integration needs Docker)
make lint               # ruff, ruff format --check, mypy --strict
uv run vc --help
```

Install the git hooks once with `uv run pre-commit install`.

## Make targets

| Target | What it does |
| --- | --- |
| `make up` / `make down` | Start / stop the local stack |
| `make lint` | Ruff lint, format check, mypy |
| `make format` | Apply Ruff fixes and formatting |
| `make test` | Unit, property and integration tests with coverage |
| `make test-unit` | Unit and property tests only (no Docker) |
| `make migrate`, `seed`, `corpus`, `eval` | Not implemented yet (see BACKLOG.md milestones) |

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and on pushes to `master`:

| Job | Checks |
| --- | --- |
| Lint and type check | `ruff check`, `ruff format --check`, `mypy --strict` |
| Unit and property tests | `pytest -m "unit or property"` with the deterministic Hypothesis `ci` profile, coverage report |
| Integration tests | Everything else, against a `pgvector/pgvector` PostgreSQL 16 service container |
| Dependency audit and secret scan | `pip-audit` on the locked dependencies, `gitleaks` over the full history |

### Branch protection

Configure once in **Settings → Branches → Add branch ruleset** for `master` (SPEC §27):

- Require a pull request before merging, with **one approving review**.
- Require status checks to pass: `Lint and type check`, `Unit and property tests`, `Integration tests`, `Dependency audit and secret scan`.
- Require branches to be up to date before merging.
- Block force pushes and deletions.
