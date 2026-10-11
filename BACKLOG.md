# Variance Copilot — Delivery Backlog

| | |
| --- | --- |
| **Source** | [SPEC.md](SPEC.md) v1.0 (section references are `§n`) |
| **Structure** | One epic per milestone (§28.1); tasks are `M<epic>-<nn>` |
| **Last updated** | 2026-10-12 |

## How to read this

- **Priority** follows §28.3: **Must** (v1.0), **Should**, **Could**.
- **Size:** **S** ≤ 2 h · **M** ≈ half a day · **L** ≥ 1 day.
- **Depends on** lists hard blockers only.
- An epic is done when its **Definition of done** holds *and* every Must task meets its acceptance criteria.
- A task's change to the spec is recorded as an ADR in `docs/adr/` and in SPEC.md in the same PR.

### Global definition of done (every task)

- [ ] Code typed and passes `ruff` and `mypy --strict` on `src/`.
- [ ] Tests added at the right layer (§20); `make test` green locally and in CI.
- [ ] No secrets, no ground-truth leakage into `corpus/`, no hard-coded expected numbers in tests or golden cases.
- [ ] Public interfaces (tool schemas, API, CLI) documented or snapshotted.

### Critical path (thin slice first, §29)

Build the `ci` data profile and three golden cases end to end before broadening:

```text
M0-01..06 → M1-01..06 → M1-07..10 → M1-11..15 (ci profile) → M1-17
  → M2-01..05, M2-10..12 → M3-01..08 → M3-10  ← first end-to-end answer
  → M5-01, M5-03, M5-09 → M7-01..06 (replay gates) → widen everything else
```

### Epic summary

| Epic | Milestone | Priority | Definition of done |
| --- | --- | --- | --- |
| [E0](#e0--bootstrap-m0) | M0 Bootstrap | Must | `make up && make test` passes locally and in CI |
| [E1](#e1--data-foundation-m1) | M1 Data foundation | Must | V-01..V-10 pass; determinism test passes; manifest and ground truth produced |
| [E2](#e2--finance-engine-and-tools-m2) | M2 Finance engine and tools | Must | Property tests pass; tool contracts snapshotted; RLS tests pass |
| [E3](#e3--agent-core-m3) | M3 Agent core | Must | End-to-end answers for three golden cases with valid placeholders |
| [E4](#e4--retrieval-and-corpus-m4) | M4 Retrieval and corpus | Must | Policy golden cases pass; citations render |
| [E5](#e5--guardrails-and-audit-m5) | M5 Guardrails and audit | Must | Security and guardrail tests pass; chain verification passes |
| [E6](#e6--api-and-review-workflow-m6) | M6 API and review | Should | API tests pass; commentary approved end to end with segregation of duties |
| [E7](#e7--evaluation-and-ci-gates-m7) | M7 Evaluation and CI gates | Must (≥25 cases) / Should (47) | Replay gates enforced in CI; first nightly report |
| [E8](#e8--deployment-and-documentation-m8) | M8 Deployment and docs | Could (docs: Must) | `cdk synth` passes; README quickstart works from a clean clone |

---

## E0 — Bootstrap (M0)

**Goal:** a reproducible dev environment and CI pipeline that every later task plugs into.

#### M0-01 Repository skeleton and packaging
- **Spec:** §22, §23 · **Priority:** Must · **Size:** S
- Create `pyproject.toml` (Python 3.12, `uv`), `src/variance_copilot/` package with the sub-packages from §23, `tests/` sub-folders, `.gitignore`, `.env.example`, `README.md` stub.
- **Acceptance criteria**
  - [ ] `uv sync` succeeds from a clean clone; `uv.lock` committed.
  - [ ] `python -c "import variance_copilot"` works inside the venv.
  - [ ] Directory tree matches §23 (empty packages have `__init__.py`).
  - [ ] `corpus/generated/`, `eval/reports/`, `generated/` and `.env` are git-ignored.

#### M0-02 Code quality tooling
- **Spec:** §21, §22 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- Configure Ruff (lint + format), mypy strict on `src/`, pre-commit hooks (ruff, mypy, gitleaks, end-of-file, trailing whitespace).
- **Acceptance criteria**
  - [ ] `make lint` runs ruff check, ruff format --check and mypy; passes on the skeleton.
  - [ ] `pre-commit run --all-files` passes.
  - [ ] A committed fake AWS key is caught by the gitleaks hook (verified once, then removed).

#### M0-03 Test harness
- **Spec:** §20 · **Priority:** Must · **Size:** M · **Depends on:** M0-01
- pytest config with markers per layer (`unit`, `property`, `integration`, `contract`, `api`, `security`), Hypothesis profile (`ci` deterministic), Testcontainers fixture for `pgvector/pgvector:pg16`, coverage config.
- **Acceptance criteria**
  - [ ] `make test` runs unit + property + integration; `make test-unit` runs without Docker.
  - [ ] A smoke integration test starts the container and runs `SELECT extversion FROM pg_extension WHERE extname='vector'`.
  - [ ] Coverage report produced; thresholds configured (finance/guardrails ≥ 95%, overall ≥ 85%) but enforced only from M2 onward.

#### M0-04 Configuration module
- **Spec:** §24 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- `config.py` with `pydantic-settings` (prefix `VC_`) and typed loaders for `config/*.yaml` (materiality, anomalies, model prices, dev users).
- **Acceptance criteria**
  - [ ] Every variable in §24 exists with the documented default.
  - [ ] Invalid values (e.g. `VC_ENV=foo`) fail fast with a clear error.
  - [ ] `VC_AUTH_MODE=dev` with `VC_ENV=prod` is rejected at startup (§16.2).
  - [ ] `config/materiality.yaml` from §9.7 and `config/dev_users.yaml` from §25 committed and load into typed models.

#### M0-05 Docker Compose and Makefile
- **Spec:** §25 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- `docker-compose.yml` (db, otel-collector, jaeger; `api` service added in M6) with pinned image versions; Makefile targets `up`, `down`, `migrate`, `seed`, `corpus`, `test`, `lint`, `eval`.
- **Acceptance criteria**
  - [ ] `make up` brings PostgreSQL + pgvector to healthy; Jaeger UI reachable on :16686.
  - [ ] All image tags pinned (no `latest`).
  - [ ] Targets not yet implemented exit non-zero with "not implemented (Mx)".

#### M0-06 CI skeleton
- **Spec:** §27 · **Priority:** Must · **Size:** M · **Depends on:** M0-02, M0-03
- `.github/workflows/ci.yml` with lint/type, unit/property, integration (pg16 service container), `pip-audit`, `gitleaks`. Later epics add their steps.
- **Acceptance criteria**
  - [ ] CI runs on every PR and push to `main`, and passes on the skeleton.
  - [ ] `uv` cache configured; total runtime < 5 min on the skeleton.
  - [ ] Branch protection on `main` documented in README (require CI + one review).

#### M0-07 CLI skeleton
- **Spec:** §17 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- Typer app `vc` with command groups `db`, `seed`, `corpus`, `ingest`, `ask`, `commentary`, `anomalies`, `eval`, `audit`, `auth` (stubs).
- **Acceptance criteria**
  - [ ] `uv run vc --help` lists all groups from §17.
  - [ ] Entry point registered in `pyproject.toml`.

#### M0-08 ADR and docs scaffolding
- **Spec:** §6.4, §23 · **Priority:** Must · **Size:** S
- ADR template plus ADR-001..008 stubs (title, status *Accepted*, context/decision/consequences copied from §6.4), `docs/architecture.md` stub with the §6.1 diagram.
- **Acceptance criteria**
  - [ ] `docs/adr/0000-template.md` and eight ADR files exist and render on GitHub.

---

## E1 — Data foundation (M1)

**Goal:** a schema matching §7.10 and a deterministic, validated synthetic ledger with ground truth.

### Schema and database access

#### M1-01 Alembic setup and base migration
- **Spec:** §7.10 (section 0) · **Priority:** Must · **Size:** S · **Depends on:** M0-03, M0-05
- Alembic configured against `VC_DATABASE_URL`; migration 0001 creates the `vector` extension, eight schemas and four roles.
- **Acceptance criteria**
  - [ ] `vc db migrate` / `make migrate` upgrades a fresh DB; `alembic downgrade base` cleans up.
  - [ ] Roles `vc_app`, `vc_tool_reader`, `vc_auditor`, `vc_eval` exist as `NOLOGIN`.
  - [ ] Migrations are idempotent in test (upgrade → downgrade → upgrade).

#### M1-02 Master data tables (`md`)
- **Spec:** §7.10 (section 1) · **Priority:** Must · **Size:** S · **Depends on:** M1-01
- **Acceptance criteria**
  - [ ] All `md.*` tables, PKs, FKs and CHECK constraints from the DDL exist; S/4HANA counterparts kept as column comments.
  - [ ] Integration test inserts a valid row per table and asserts each CHECK rejects an invalid one (e.g. `gl_account` type `X` with a P&L category).

#### M1-03 Transaction and plan tables (`fin`)
- **Spec:** §7.10 (section 2) · **Priority:** Must · **Size:** S · **Depends on:** M1-02
- **Acceptance criteria**
  - [ ] `period_status`, `journal_header`, `journal_line`, `plan_version`, `plan_line`, `stat_key_figure` and all five `jl_*` indexes plus `pl_lookup_idx` exist.
  - [ ] Debit/credit sign CHECK rejects `S` with negative `amount_lc` and `H` with positive.

#### M1-04 Semantic views, RLS and grants
- **Spec:** §7.10 (sections 3–4), §7.11 · **Priority:** Must · **Size:** M · **Depends on:** M1-03
- `rpt.pnl_actual`, `rpt.pnl_plan` (security_invoker, presentation sign), `rpt.unbalanced_documents`; RLS policies and grants.
- **Acceptance criteria**
  - [ ] Views flip sign (`presented = −stored`) — tested with one revenue and one expense line.
  - [ ] As `vc_tool_reader` with `app.company_scope='ES10'`, only ES10 rows are visible through tables **and** views.
  - [ ] With the setting missing, zero rows are returned (deny by default).
  - [ ] `vc_tool_reader` cannot read `audit`, `review`, `truth` or `app`.

#### M1-05 Documents, app, audit, review and truth tables
- **Spec:** §7.10 (sections 5–6) · **Priority:** Must · **Size:** M · **Depends on:** M1-01
- **Acceptance criteria**
  - [ ] `doc.chunk` has `vector(VC_EMBEDDING_DIM)`, generated `tsv`, HNSW and GIN indexes.
  - [ ] UPDATE, DELETE and TRUNCATE on `audit.event` and `audit.fact` raise `audit tables are append-only`.
  - [ ] `review.commentary` rejects `approved_by = created_by`.
  - [ ] Only `vc_eval` can read `truth`; `PUBLIC` has no access.

#### M1-06 Database session layer with RLS context
- **Spec:** §7.10 (session context), §13.6 · **Priority:** Must · **Size:** M · **Depends on:** M1-04
- `db/` module: engine, sessions, and a `tool_transaction(company_scope)` context manager that runs `SET LOCAL ROLE vc_tool_reader`, `statement_timeout = 5s` and `set_config('app.company_scope', …, true)`.
- **Acceptance criteria**
  - [ ] Scope is transaction-local: a pooled connection reused after commit sees no scope.
  - [ ] Statement timeout enforced (a `pg_sleep(6)` query fails).
  - [ ] Scope values are passed as bind parameters, never string-interpolated.

### Seed data generator

#### M1-07 Master data YAML and loader
- **Spec:** §7.3–§7.8, §8.3 step 1 · **Priority:** Must · **Size:** M · **Depends on:** M1-02
- `seeds/master/*.yaml`: currencies, controlling area, five company codes, segments, functional areas, chart of accounts AUR1, ~45 cost centers, profit centers, six brands, LoE dates, materials, customers, vendors (incl. named scenario vendors), WBS elements.
- **Acceptance criteria**
  - [ ] Every account, cost center and brand listed in §7.4–§7.7 is present with the listed attributes.
  - [ ] Loader is idempotent (re-running yields identical rows).
  - [ ] All names are fictional; no real company names.

#### M1-08 Fiscal calendar and period status
- **Spec:** §8.2, §8.3 step 2 · **Priority:** Must · **Size:** S · **Depends on:** M1-07
- **Acceptance criteria**
  - [ ] FY2025 P01–P13 and FY2026 P01–P09 `CLOSED`; FY2026 P10 `OPEN`.
  - [ ] `closed_at` = working day 4 of the following month, 18:00 local time per company code.
  - [ ] Special periods 13–16 exist with `is_special = true`.

#### M1-09 Exchange-rate paths
- **Spec:** §7.8, §8.6 · **Priority:** Must · **Size:** M · **Depends on:** M1-07, M1-10
- **Acceptance criteria**
  - [ ] `B25`, `B26` match §8.6 exactly; `F126` = March 2026 `AVG`, `F226` = June 2026 `AVG`.
  - [ ] CHF 2026 follows the E02 override within ±0.3%; other `AVG` paths stay within ±4% of budget.
  - [ ] `CLO(t)` = `AVG(t+1)` × (1 + noise, σ 0.4%).
  - [ ] Same seed → identical rates.

#### M1-10 Scenario configuration and profiles
- **Spec:** §8.1, §8.12, Appendix A · **Priority:** Must · **Size:** M · **Depends on:** M0-04
- `seeds/scenario/fy2025_fy2026.yaml` (drivers, calibration targets, events E01–E12) and `seeds/profiles.yaml` (`full`, `dev`, `ci`); typed Pydantic models; RNG plumbing via `SeedSequence.spawn` per module.
- **Acceptance criteria**
  - [ ] Config validates against Pydantic models; invalid config fails with a path to the bad field.
  - [ ] Each generator module receives its own child RNG; adding a module does not change other modules' streams.

#### M1-11 Plan drivers and budget generation
- **Spec:** §8.4, §8.5, §8.3 step 4 · **Priority:** Must · **Size:** L · **Depends on:** M1-09, M1-10
- Revenue model (lifecycle curves, LoE erosion, seasonality, list price, GTN split), transfer prices, standard costs, FTE plan, cost per FTE, marketing/medical budgets, trial curves, asset register.
- **Acceptance criteria**
  - [ ] Unit tests for each lifecycle curve, LoE erosion and GTN split.
  - [ ] `BUD26` assumes CARDIVEX US LoE on 1 September 2026 (as MEMO-BUD26 will state).
  - [ ] Budget cost structure within ±3 pp of the §8.4 targets (COGS 22%, S&M 25%, R&D 22%, G&A 5%).

#### M1-12 Actual drivers and noise
- **Spec:** §8.3 step 5, §8.9 · **Priority:** Must · **Size:** M · **Depends on:** M1-11
- **Acceptance criteria**
  - [ ] Noise parameters match §8.9 table; noise generated once and frozen per seed.
  - [ ] Event overlays applied as separate layers so the ground-truth impact of each event can be isolated.

#### M1-13 Posting engine
- **Spec:** §8.7, §7.12 · **Priority:** Must · **Size:** L · **Depends on:** M1-08, M1-12
- Document types RV, ZR, WE, WA, RI, KR, SA, AF, AL; intercompany chain IE10 → CH10 → affiliates; GBS fees; payroll on the 25th; month-end accruals with auto-reversal; allocations by FTE; depreciation; TC→LC→GC translation; rounding residual to the largest line.
- **Acceptance criteria**
  - [ ] Every document balances in LC and GC (I-01).
  - [ ] No `entry_ts` after the period's `closed_at` (I-02); posting dates inside period (I-03).
  - [ ] Every accrual has a reversal on day 1 of the next period linked via `reversal_document` (I-04).
  - [ ] Intercompany revenue = buyer expense per seller/buyer/period within USD 1 (I-05); no IC inventory build (I-07).
  - [ ] Functional area on each line follows §7.4 derivation (I-08).
  - [ ] Billing weekly in `full`, monthly in `dev`/`ci`.

#### M1-14 Plan lines and statistical key figures
- **Spec:** §8.3 step 8, §7.9 · **Priority:** Must · **Size:** M · **Depends on:** M1-11
- **Acceptance criteria**
  - [ ] `fin.plan_version` rows for `BUD25`, `BUD26`, `FC126`, `FC226` per §7.9.
  - [ ] Plan GC translated at the version's rate type.
  - [ ] FTE and HEADCOUNT stored in `fin.stat_key_figure` for `ACT` and every plan version.

#### M1-15 Scenario events and ground truth
- **Spec:** §8.8, Appendix A · **Priority:** Must · **Size:** L · **Depends on:** M1-13, M1-14
- Implement E01–E12 (the `ci` profile needs E01, E02, E05, E06, E07 first); record documents and exact impact per plan version, per period and YTD in `truth.scenario_event`.
- **Acceptance criteria**
  - [ ] Each event's impact lands within its §8.8 target range.
  - [ ] E04b milestone is capitalised (13000000 / 20000000, TC USD) with zero P&L impact.
  - [ ] E06 nets to zero YTD; E10 nets to zero at group level.
  - [ ] E07 lines carry assignment `TRUEUP-2026P01-P07`.
  - [ ] E12: 25 decoys below materiality; NEURELIS within ±2% of budget.
  - [ ] `truth.scenario_event.documents` references real documents.

#### M1-16 Forecast generation
- **Spec:** §8.10 · **Priority:** Must · **Size:** M · **Depends on:** M1-15
- **Acceptance criteria**
  - [ ] `FC126` has actuals P01–P03; `FC226` has actuals P01–P06; remaining months re-planned.
  - [ ] Event knowledge per version follows the §8.10 table exactly (unit-tested per event).
  - [ ] Opex run-rate blended 50/50 budget phasing and trailing 3-month actuals.
  - [ ] Variance vs `FC226` is small for E01/E02 and large for E05/E07.

#### M1-17 Generated-data validation suite
- **Spec:** §8.13 · **Priority:** Must · **Size:** M · **Depends on:** M1-15, M1-16
- **Acceptance criteria**
  - [ ] V-01..V-10 implemented as SQL/Python checks; generation aborts on any failure with the check ID and sample offending rows.
  - [ ] `vc seed verify` re-runs all checks against the current database.
  - [ ] V-10 regenerates and compares manifest checksums.

#### M1-18 Export, manifest and CLI
- **Spec:** §8.11, §17 · **Priority:** Must · **Size:** S · **Depends on:** M1-17
- **Acceptance criteria**
  - [ ] `vc seed --profile ci --seed 20261010` loads data and writes `generated/manifest.json` (seed, profile, generator version, git commit, row counts, SHA-256 per table) and `generated/ground_truth.json`.
  - [ ] `ci` profile: 10k–15k journal lines, < 60 s; `dev` profile: 50k–70k lines.
  - [ ] Generator `ci` profile + V-01..V-10 runs in CI.

#### M1-19 Calibration to FY2025 targets
- **Spec:** §8.4 calibration table · **Priority:** Must · **Size:** M · **Depends on:** M1-15
- **Acceptance criteria**
  - [ ] FY2025 net sales per brand within ±3% of targets (V-07), group ≈ USD 6.3bn.
  - [ ] FTEs per company within ±5% of §8.5 figures.

---

## E2 — Finance engine and tools (M2)

**Goal:** every number the system shows comes from tested, deterministic code and is registered as a fact.

### Finance engine (`variance_copilot.finance`)

#### M2-01 Decimal context and formatting
- **Spec:** §9.10 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- **Acceptance criteria**
  - [ ] Shared `Decimal` context (28 digits); `ROUND_HALF_UP` only in formatting.
  - [ ] Formatter covers bn/m/k/units, `%` (1 dp), `pp` (1 dp), FTE (whole), rates (4 dp), leading `−` and `abs`.
  - [ ] Table-driven tests cover unit boundaries (e.g. how 999,950 rounds across the k/m threshold — rule documented), zero and negatives.

#### M2-02 Periods and version resolution
- **Spec:** §9.11, §7.9, §10.3 · **Priority:** Must · **Size:** M · **Depends on:** M1-06
- **Acceptance criteria**
  - [ ] MTD, QTD, YTD, FY and explicit ranges resolve to period lists; special periods only in FY and YTD P12.
  - [ ] `LATEST_FC` resolves to the approved forecast with the highest `actuals_through_period`; `PY` to the same periods of the prior year.
  - [ ] Any `OPEN`/`CLOSING` period produces `OPEN_PERIOD`.

#### M2-03 Translation and constant currency
- **Spec:** §9.2 · **Priority:** Must · **Size:** M · **Depends on:** M2-02
- **Acceptance criteria**
  - [ ] CC vs plan uses the version's rate type; CC vs PY uses prior-year `AVG` by month.
  - [ ] Property test: `Δ_total = Δ_op + FX` for random inputs.
  - [ ] USD entity: `A_cc = A_gc`, `FX = 0`.

#### M2-04 Variance core and materiality
- **Spec:** §9.3, §9.7 · **Priority:** Must · **Size:** S · **Depends on:** M2-01
- **Acceptance criteria**
  - [ ] `Δ`, `Δ%` (null + `NO_COMPARISON_BASE` when `C = 0`), `favorable`, `magnitude` per §9.3.
  - [ ] Materiality rule per §9.7 for group and company-code levels, read from `config/materiality.yaml`.
  - [ ] Pareto drill-down respects `pareto_coverage`, `max_contributors`, `min_contributor`.

#### M2-05 Revenue bridge
- **Spec:** §9.4 · **Priority:** Must · **Size:** L · **Depends on:** M2-03
- **Acceptance criteria**
  - [ ] Volume, mix, list price, GTN rate, GTN prior-period (`TRUEUP-`), other revenue, FX components.
  - [ ] Property test: components sum to the net sales variance; `rounding` < USD 1,000.
  - [ ] Unplanned SKU → `UNPLANNED_VOLUME`; `Q^A = 0` → volume = −C.
  - [ ] Intercompany revenue excluded at group scope, shown separately at entity scope.
  - [ ] On generated data, E01 shows up as volume + price and E07 as GTN prior-period.

#### M2-06 Operating-expense bridge
- **Spec:** §9.5 · **Priority:** Must · **Size:** M · **Depends on:** M2-03
- **Acceptance criteria**
  - [ ] FX, FTE volume, personnel rate, non-personnel by account, allocations, other operating items, memo one-offs.
  - [ ] Property test: components (excluding memo) sum to the opex variance.
  - [ ] E04a appears under 61070000 on CH10-RD-ONC; E04b does not appear.

#### M2-07 Core reconciliation
- **Spec:** §9.6 · **Priority:** Must · **Size:** S · **Depends on:** M2-04
- **Acceptance criteria**
  - [ ] Core OI = IFRS OI − Σ presented core-adjustment amounts, by category.
  - [ ] E08 restructuring raises core vs IFRS; E11 divestment gain lowers it (FY2025).

#### M2-08 Full-year outlook and timing classification
- **Spec:** §9.8 · **Priority:** Must · **Size:** M · **Depends on:** M2-02, M2-04
- **Acceptance criteria**
  - [ ] `TIMING` / `PERMANENT` / `PARTIAL_TIMING` / `UNDETERMINED` thresholds per §9.8.
  - [ ] Reversal pairs from rule A4 are classified `TIMING`.
  - [ ] E06 and E09 classify as `TIMING`; E05 as `PERMANENT`.

#### M2-09 Anomaly rules A1–A8
- **Spec:** §9.9 · **Priority:** Must · **Size:** L · **Depends on:** M2-04
- **Acceptance criteria**
  - [ ] Each rule implemented with thresholds from `config/anomalies.yaml`.
  - [ ] Findings include rule, severity, document evidence, amount (as a fact) and a suggested reviewer question worded as something to investigate.
  - [ ] E06 triggers A1 and A2; E09 triggers A3; E06's P07 reclass triggers A4; E12 decoys trigger nothing above threshold.

### Tool layer (`variance_copilot.tools`)

#### M2-10 Fact ledger
- **Spec:** §10.2, §7.10 `audit.fact` · **Priority:** Must · **Size:** M · **Depends on:** M1-05
- **Acceptance criteria**
  - [ ] Fact IDs `F<call>-<seq>`, unique per run; kinds and units per DDL.
  - [ ] Variance facts carry `favorable` and `magnitude`; provenance includes tool call ID, tool, arguments SHA-256, row key, field.
  - [ ] Facts persisted to `audit.fact` in the same transaction as the `FACTS_REGISTERED` event (once M5-01 exists).

#### M2-11 Tool framework
- **Spec:** §10.1, §10.3 (common types) · **Priority:** Must · **Size:** M · **Depends on:** M1-06, M2-10
- Registry, Pydantic input models (`scope`, `period`, `currency_basis`, `basis`, versions), effective-scope echo, warnings, structured errors, limits, deterministic ordering, JSON-schema export for the LLM.
- **Acceptance criteria**
  - [ ] Each call runs in its own RLS transaction; 10 s tool timeout.
  - [ ] `GROUP` scope requires `group_analyst` and excludes trading-partner lines (I-06).
  - [ ] Errors return `{error: {code, message}}`, never raise to the loop.
  - [ ] Output order is stable (|Δ| desc, then key) — tested by running twice.
  - [ ] No free-form SQL inputs anywhere.

#### M2-12 Data tools
- **Spec:** §10.3, §10.4 · **Priority:** Must · **Size:** L · **Depends on:** M2-11, M2-02..04
- `list_dimension_members`, `get_period_status`, `get_pnl`, `compare`, `journal_lines`, `get_fx_rates`.
- **Acceptance criteria**
  - [ ] `compare` output matches the §10.4 contract shape.
  - [ ] `journal_lines` caps at 200 rows and sets `TRUNCATED`.
  - [ ] `PARTIAL_SCOPE` when requested company codes exceed visible ones; `NO_DATA` on empty results.

#### M2-13 Analysis tools
- **Spec:** §10.3 · **Priority:** Must · **Size:** L · **Depends on:** M2-05..09, M2-11
- `revenue_bridge`, `opex_bridge`, `top_contributors`, `full_year_outlook`, `core_reconciliation`, `detect_anomalies`.
- **Acceptance criteria**
  - [ ] Each returns facts for every number and echoes effective scope.
  - [ ] `top_contributors` capped at 20 rows and reports coverage.

#### M2-14 RLS and access tests
- **Spec:** §7.10, §13.6, §18.1 · **Priority:** Must · **Size:** M · **Depends on:** M2-12
- **Acceptance criteria**
  - [ ] ES10 analyst cannot see US10 rows through any tool, including via filters on account, document number or text search.
  - [ ] Bypass attempts fail: `RESET ROLE`, `set_config` from tool inputs, SQL in string fields.
  - [ ] Group totals exclude every trading-partner line; E10 shows zero group impact.

#### M2-15 Tool contract snapshots
- **Spec:** §20 (contract) · **Priority:** Must · **Size:** S · **Depends on:** M2-12, M2-13
- **Acceptance criteria**
  - [ ] JSON schemas of all tool inputs and outputs snapshotted in `tests/contract/`.
  - [ ] CI fails on an unreviewed schema change (snapshot update needs an explicit flag).

#### M2-16 Tool performance on the `full` profile
- **Spec:** §21 · **Priority:** Should · **Size:** M · **Depends on:** M2-13
- **Acceptance criteria**
  - [ ] Benchmark script reports tool p95; all tools ≤ 500 ms on `full` (350k–450k lines).
  - [ ] Any extra index added via migration with justification.

---

## E3 — Agent core (M3)

**Goal:** a question goes in, a validated answer built only from facts comes out.

#### M3-01 Provider interface
- **Spec:** §12.2 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- **Acceptance criteria**
  - [ ] Dataclasses and `LLMProvider` / `EmbeddingProvider` protocols exactly as §12.2.
  - [ ] Provider selected from `VC_LLM_PROVIDER`.

#### M3-02 First live provider
- **Spec:** §12.2, OD-1 · **Priority:** Must · **Size:** M · **Depends on:** M3-01
- Decide OD-1 and implement that provider (tool use, structured output, usage, stop reasons, retries with backoff + jitter).
- **Acceptance criteria**
  - [ ] OD-1 decision recorded as an ADR.
  - [ ] Tool calls and the `Answer` schema round-trip correctly in a live smoke test (marked, skipped without a key).
  - [ ] Three retries on provider errors, then a typed failure.

#### M3-03 Replay and record providers
- **Spec:** §19.5, ADR-007 · **Priority:** Must · **Size:** M · **Depends on:** M3-01
- **Acceptance criteria**
  - [ ] Request key = SHA-256 of canonical JSON (messages, tool schemas, parameters, model).
  - [ ] `record` stores gzipped JSON in `eval/cassettes/`; `replay` returns it byte-for-byte.
  - [ ] A missing recording fails with the request hash and instructions to record.

#### M3-04 Structured output schemas
- **Spec:** §12.4 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- **Acceptance criteria**
  - [ ] `ScopeEcho`, `Driver`, `Answer`, `CommentarySection`, `Commentary` as §12.4, including length limits.
  - [ ] JSON schemas exported and snapshotted.

#### M3-05 Prompt management
- **Spec:** §12.5 · **Priority:** Must · **Size:** S · **Depends on:** M0-01
- **Acceptance criteria**
  - [ ] `prompts/system/qa.md` and `prompts/repair.md` with front matter (`id`, `version`, `owner`, `changelog`).
  - [ ] Loader returns rendered text and SHA-256; SHA stored on every run.

#### M3-06 Agent loop
- **Spec:** §6.2, §12.3, §12.6, §15.5 · **Priority:** Must · **Size:** L · **Depends on:** M2-12, M3-01, M3-04, M3-05
- **Acceptance criteria**
  - [ ] Limits from §12.3 enforced (turns, tool calls, parallelism 4, wall clock, output tokens, cost, one repair).
  - [ ] Cost computed from `config/model_prices.yaml` after every call; run aborts with `ABORTED_BUDGET` when exceeded.
  - [ ] Temperature 0; model ID pinned per environment.
  - [ ] `app.run` row created and updated with status, tokens, cost, output.
  - [ ] Tool results cached per run by `(tool, arguments, data_version)`.
  - [ ] Unit tests with a scripted fake provider cover each limit.

#### M3-07 Renderer
- **Spec:** §13.1, §11.7, §14 rule 6, Appendix C · **Priority:** Must · **Size:** M · **Depends on:** M2-01, M3-04
- **Acceptance criteria**
  - [ ] Placeholders `{{F}}`, `|abs`, `|pct`, `|abspct`, `|pp` render per §9.10; incompatible modifier is an error.
  - [ ] Citations render as `(POL-005 §4.2)`.
  - [ ] Rendered Markdown matches the Appendix C layout (headline, scope line, numbered drivers, caveats, footer label).
  - [ ] Placeholder-to-text mapping returned for the `VALIDATION` event.

#### M3-08 `vc ask` and dev users
- **Spec:** §17, §25 · **Priority:** Must · **Size:** S · **Depends on:** M3-06, M3-07
- **Acceptance criteria**
  - [ ] `vc ask "<question>" --user analyst_us` prints rendered Markdown; `--json` prints the structured answer + run ID.
  - [ ] Company scope comes from `config/dev_users.yaml`, never from the question.

#### M3-09 Commentary prompt
- **Spec:** §6.3, §12.5 · **Priority:** Should · **Size:** S · **Depends on:** M3-05
- **Acceptance criteria**
  - [ ] `prompts/system/commentary.md` versioned, with the same number and citation rules as Q&A.

#### M3-10 Thin-slice end-to-end
- **Spec:** §28.1 M3 DoD · **Priority:** Must · **Size:** M · **Depends on:** M3-08, M1-18
- **Acceptance criteria**
  - [ ] Three golden cases (suggested: QA-E01-001, QA-E05, QA-E06) answered on the `ci` profile with all placeholders resolving.
  - [ ] Recordings committed; the same three run green in CI via replay.

---

## E4 — Retrieval and corpus (M4)

**Goal:** policy claims are grounded in retrieved, citable chunks; memos never contradict the data.

#### M4-01 Policies, procedure and glossary
- **Spec:** §11.1, §11.2 · **Priority:** Must · **Size:** L
- POL-001..008, PROC-001, REF-GLOSSARY in `corpus/policies/`.
- **Acceptance criteria**
  - [ ] Each has front matter per §11.2 and sections purpose/scope/definitions/rules/examples.
  - [ ] Content supports the events listed in §11.1 (e.g. POL-005 says milestones for separately acquired rights are capitalised).
  - [ ] No event IDs, `truth` references or evaluation answers.

#### M4-02 Memo and commentary templates
- **Spec:** §11.1, §11.2 · **Priority:** Must · **Size:** M · **Depends on:** M1-10
- MEMO-BUD26, MEMO-FC126, MEMO-FC226, MEMO-TP26, COM-2025-P12, COM-2026-P06 as Jinja2 templates; `vc corpus render`.
- **Acceptance criteria**
  - [ ] Rendered from scenario parameters; e.g. MEMO-BUD26 states LoE 1 September, MEMO-FC226 states 1 May.
  - [ ] Output in `corpus/generated/` (git-ignored); rendering is deterministic.

#### M4-03 Adversarial documents
- **Spec:** §11.1, §13.5 · **Priority:** Must · **Size:** S
- **Acceptance criteria**
  - [ ] ADV-001 contains an embedded instruction to misreport figures; ADV-002 is a superseded GTN policy with past `effective_to`.
  - [ ] Stored in `corpus/adversarial/`, ingested only for evaluation.

#### M4-04 Ingestion pipeline
- **Spec:** §11.3 · **Priority:** Must · **Size:** M · **Depends on:** M1-05, M4-05, M4-06
- **Acceptance criteria**
  - [ ] `vc ingest` parses front matter + body, normalises, hashes; unchanged docs skipped.
  - [ ] Document and chunks upserted in one transaction.
  - [ ] `INGESTION` audit event with IDs, versions, hashes, chunk counts.
  - [ ] `--reembed` re-embeds all chunks.

#### M4-05 Chunker
- **Spec:** §11.4 · **Priority:** Must · **Size:** M
- **Acceptance criteria**
  - [ ] Heading-first split, `section_path` trail, 300–400 tokens, 60-token overlap.
  - [ ] Tables and numbered rule lists never split mid-item (tested with fixtures).
  - [ ] Chunk IDs `<doc_id>#<ordinal>`, stable across re-runs.

#### M4-06 Embedding providers
- **Spec:** §11.5, OD-2 · **Priority:** Must · **Size:** M · **Depends on:** M3-01
- **Acceptance criteria**
  - [ ] OD-2 decided and recorded; dimension matches `VC_EMBEDDING_DIM` and the migration (startup check).
  - [ ] Batch size 64, backoff retries, cache by content hash.
  - [ ] A deterministic fake embedder exists for tests and CI.

#### M4-07 Hybrid retrieval
- **Spec:** §11.6 · **Priority:** Must · **Size:** M · **Depends on:** M4-04
- **Acceptance criteria**
  - [ ] Lexical top 20 + semantic top 20, fused with RRF (k = 60); return top k (default 6, max 8).
  - [ ] Filters: effective dates vs `as_of`, company codes overlap user scope, doc type, `suspicious` excluded by default.
  - [ ] ADV-002 never returned for an `as_of` after its `effective_to`.
  - [ ] RRF unit-tested; retrieval p95 ≤ 300 ms on the full corpus.

#### M4-08 `search_documents` tool and citations
- **Spec:** §10.3, §11.6, §11.7 · **Priority:** Must · **Size:** S · **Depends on:** M4-07, M2-11
- **Acceptance criteria**
  - [ ] Tool returns chunk IDs, titles, section paths, scores; content wrapped as untrusted (§13.5).
  - [ ] `RETRIEVAL` audit event with query, filters, chunk IDs, scores.
  - [ ] Policy golden cases (UC-08) answered with rendered citations.

#### M4-09 Corpus leakage scan
- **Spec:** §11.2, §27 step 7 · **Priority:** Must · **Size:** S
- **Acceptance criteria**
  - [ ] CI fails if `corpus/` (including rendered memos) contains `E0x`/`E1x` event IDs or the `truth` schema name.

---

## E5 — Guardrails and audit (M5)

**Goal:** no ungrounded number or citation reaches a user, and every run is tamper-evident.

#### M5-01 Hash-chained audit writer
- **Spec:** §15.1, §15.2 · **Priority:** Must · **Size:** M · **Depends on:** M1-05
- **Acceptance criteria**
  - [ ] `row_hash = SHA-256(prev_hash ‖ JCS(event))` with RFC 8785 canonicalisation; genesis `prev_hash` = 64 zeros.
  - [ ] Inserts serialised with `pg_advisory_xact_lock`; concurrent writers produce a valid chain (stress test).
  - [ ] All event types from §15.1 have typed payload models.

#### M5-02 `vc audit verify`
- **Spec:** §15.2, UC-06 · **Priority:** Must · **Size:** S · **Depends on:** M5-01
- **Acceptance criteria**
  - [ ] Recomputes the chain over `--from/--to`; reports the first break.
  - [ ] Test: tampering with a payload (as superuser, triggers disabled) is detected.

#### M5-03 Numeric grounding validator
- **Spec:** §13.2 · **Priority:** Must · **Size:** M · **Depends on:** M3-07
- **Acceptance criteria**
  - [ ] Rejects any raw numeral outside placeholders except the §13.2 allowlist (years, periods, ranges, dates, codes, document refs, policy refs).
  - [ ] Every placeholder resolves to a fact in the same run; modifier fits the kind; `impact_fact_id` is a variance fact.
  - [ ] Table-driven tests for each allowlist pattern and known false-positive traps (e.g. "Q3", "IFRS 15", "RES-200MCG-1").

#### M5-04 Direction consistency check
- **Spec:** §13.3 · **Priority:** Must · **Size:** M · **Depends on:** M5-03
- **Acceptance criteria**
  - [ ] Nearest direction word within six tokens checked against `favorable` / `magnitude` per the lexicon.
  - [ ] "R&D costs were {{X|abs}} above budget" passes for an unfavourable `UP` fact; "sales were {{X|abs}} above budget" fails for a `DOWN` fact.

#### M5-05 Citation validator
- **Spec:** §13.4 · **Priority:** Must · **Size:** S · **Depends on:** M4-08
- **Acceptance criteria**
  - [ ] Cited chunks must come from a retrieval in the same run.
  - [ ] Drivers classified `GTN_PRIOR_PERIOD`, `CORE_ADJUSTMENT`, `RESTRUCTURING`, `INTERCOMPANY`, or mentioning policy/IFRS/IAS/capitalisation, must cite ≥ 1 chunk.

#### M5-06 Scope classifier
- **Spec:** §13.5, UC-09 · **Priority:** Must · **Size:** M · **Depends on:** M3-06
- **Acceptance criteria**
  - [ ] Labels `IN_SCOPE_ANALYSIS`, `POLICY`, `OUT_OF_SCOPE`, `PERSONAL_DATA`, `UNSAFE`; rules first, LLM only if inconclusive.
  - [ ] Share-price prediction and individual salary requests refused briefly; `SCOPE_CLASSIFIED` event records label and method.

#### M5-07 Prompt-injection defences
- **Spec:** §13.5, §18.1 · **Priority:** Must · **Size:** M · **Depends on:** M4-04, M4-08
- **Acceptance criteria**
  - [ ] Retrieved content wrapped in delimiters with `trust="untrusted"` and doc ID.
  - [ ] Ingestion flags imperative AI-directed instructions as `suspicious`; ADV-001 is flagged.
  - [ ] With `include_flagged = true`, the model ignores ADV-001's instruction and mentions it (eval case SEC-INJECT-001).

#### M5-08 Access guard
- **Spec:** §13.6, UC-07 · **Priority:** Must · **Size:** S · **Depends on:** M3-06
- **Acceptance criteria**
  - [ ] Out-of-scope entities in the question → "No access to US10", no query issued, `ACCESS_DENIED` event.
  - [ ] Non-group users asking about the group get the "authorised scope only" label.

#### M5-09 Repair loop and deterministic fallback
- **Spec:** §13.7 · **Priority:** Must · **Size:** M · **Depends on:** M5-03..05
- **Acceptance criteria**
  - [ ] One repair turn listing exact violations (`REPAIR_REQUESTED`).
  - [ ] Second failure → `FAILED_VALIDATION` and a finance-engine table with a fixed message, no model text.
  - [ ] Failed commentary is never stored as a draft.

#### M5-10 Audit wiring across the run
- **Spec:** §15.1, §15.3 · **Priority:** Must · **Size:** M · **Depends on:** M5-01, M3-06
- **Acceptance criteria**
  - [ ] Every run emits `RUN_STARTED` … `RUN_COMPLETED`/`RUN_FAILED` with the payloads in §15.1.
  - [ ] `VC_STORE_LLM_CONTENT` controls full content; hashes always stored.
  - [ ] Questions appear in audit only, never in application logs.

#### M5-11 Observability
- **Spec:** §15.4 · **Priority:** Should · **Size:** M · **Depends on:** M3-06
- **Acceptance criteria**
  - [ ] OTel spans `vc.run` → `vc.llm.call` / `vc.tool.<name>` / `vc.retrieval` with GenAI attributes, visible in Jaeger.
  - [ ] Metrics from §15.4 exported; structlog JSON logs carry `trace_id`, `run_id`, `request_id`; secrets redacted.

#### M5-12 Security test suite
- **Spec:** §18.1, §20 (security) · **Priority:** Must · **Size:** M · **Depends on:** M5-01, M2-14
- **Acceptance criteria**
  - [ ] Tests for audit immutability, role grants (tool role cannot read `audit`/`truth`), scope labelling, injection fixtures.
  - [ ] Each threat row in §18.1 maps to at least one test or a documented control.

---

## E6 — API and review workflow (M6)

**Goal:** the service is usable over REST and commentary flows from draft to approved with segregation of duties.

#### M6-01 FastAPI app and conventions
- **Spec:** §16.1, §16.3 (health) · **Priority:** Should · **Size:** M · **Depends on:** M0-04
- **Acceptance criteria**
  - [ ] `/v1` routing, `/openapi.json`, `X-Request-ID` echo, RFC 9457 problem details, cursor pagination (max 100), decimals as strings.
  - [ ] `/healthz` and `/readyz` (DB + provider config).

#### M6-02 Authentication and RBAC
- **Spec:** §3, §16.2 · **Priority:** Should · **Size:** M · **Depends on:** M6-01
- **Acceptance criteria**
  - [ ] Dev HS256 tokens via `vc auth dev-token --user <name>`; disabled when `VC_ENV=prod`.
  - [ ] OIDC: JWKS signature, issuer, audience, expiry; reads `sub`, `vc_roles`, `vc_company_scope`.
  - [ ] Role matrix from §16.3 enforced; admin has no finance data access.

#### M6-03 Idempotency and rate limits
- **Spec:** §16.1 · **Priority:** Should · **Size:** S · **Depends on:** M6-02
- **Acceptance criteria**
  - [ ] `Idempotency-Key` on question and commentary POSTs returns the same run within 24 h per user.
  - [ ] 30 questions/min, 5 commentaries/min per user; `429` + `Retry-After`.

#### M6-04 Question and run endpoints
- **Spec:** §16.3, §16.4, OD-3 · **Priority:** Should · **Size:** M · **Depends on:** M6-02, M5-10
- **Acceptance criteria**
  - [ ] `POST /v1/questions` returns rendered + structured answer and run ID (synchronous, 90 s, unless OD-3 decides otherwise).
  - [ ] `GET /v1/runs/{id}`, `/facts`, `/trace` with role and scope checks.

#### M6-05 Commentary pipeline
- **Spec:** §6.3, UC-02 · **Priority:** Should · **Size:** L · **Depends on:** M2-13, M3-09, M4-08, M5-09
- **Acceptance criteria**
  - [ ] Variance pack built deterministically (comparison, materiality, bridges, anomalies, outlook, core reconciliation) before any LLM call.
  - [ ] Deterministic policy retrieval per accounting-dependent classification.
  - [ ] At most six extra drill-down calls; output validated and stored as `DRAFT` revision 1.
  - [ ] Every material line explained; immaterial lines not presented as drivers.

#### M6-06 Review workflow
- **Spec:** §14, UC-05 · **Priority:** Should · **Size:** M · **Depends on:** M6-05
- **Acceptance criteria**
  - [ ] State machine per §14; illegal transitions rejected.
  - [ ] Approver ≠ creator (DB) and ≠ any editor of the approved revision (app).
  - [ ] Human edits re-validated; overrides need a justification (`VALIDATION_OVERRIDE`).
  - [ ] Approved revisions immutable; newer approval supersedes the previous one.
  - [ ] Draft and approved labels per §14 rule 6.

#### M6-07 Commentary endpoints
- **Spec:** §16.3 · **Priority:** Should · **Size:** M · **Depends on:** M6-06
- **Acceptance criteria**
  - [ ] Create (`202`), list, get, revisions, submit, approve, reject with role and scope checks.
  - [ ] Reviewers act only on commentaries whose scope ⊆ their own.

#### M6-08 Data and search endpoints
- **Spec:** §16.3, UC-03, UC-04 · **Priority:** Should · **Size:** S · **Depends on:** M6-02, M2-13, M4-07
- **Acceptance criteria**
  - [ ] `GET /v1/anomalies`, `GET /v1/journal-lines` (≤ 200, truncation flag), `POST /v1/documents/search` — no LLM involved.

#### M6-09 Admin and auditor endpoints
- **Spec:** §16.3 · **Priority:** Should · **Size:** S · **Depends on:** M6-02, M5-02, M4-04
- **Acceptance criteria**
  - [ ] `POST /v1/admin/ingest`; `POST /v1/admin/prompts/{id}/publish` requires a passing eval reference and writes `PROMPT_PUBLISHED`.
  - [ ] `GET /v1/audit/verify` for auditors.

#### M6-10 Remaining CLI commands
- **Spec:** §17 · **Priority:** Should · **Size:** S · **Depends on:** M6-06, M2-13
- **Acceptance criteria**
  - [ ] `vc commentary create/submit/approve/reject`, `vc anomalies` work against the same services as the API.

#### M6-11 API tests and contract snapshots
- **Spec:** §20 (API, contract) · **Priority:** Should · **Size:** M · **Depends on:** M6-04..09
- **Acceptance criteria**
  - [ ] httpx `AsyncClient` tests for auth, RBAC, idempotency, problem details on every endpoint.
  - [ ] OpenAPI document snapshotted; breaking changes fail CI.

#### M6-12 Container image
- **Spec:** §22, §27 · **Priority:** Should · **Size:** S · **Depends on:** M6-01
- **Acceptance criteria**
  - [ ] Multi-stage Dockerfile, non-root user; `api` service added to Compose.
  - [ ] Docker build runs in CI; image scan has no critical/high findings.

---

## E7 — Evaluation and CI gates (M7)

**Goal:** quality is measured on every PR and regressions block merges.

#### M7-01 Golden case schema and loader
- **Spec:** §19.2, Appendix B · **Priority:** Must · **Size:** S
- **Acceptance criteria**
  - [ ] Pydantic model for the Appendix B format (persona, mode, question, `context_from`, expected status/scope/drivers/facts/citations, `must_state`, tags, split).
  - [ ] Loader rejects hard-coded expected numbers.
  - [ ] `holdout` cases excluded from local commands unless `--include-holdout`.

#### M7-02 Expected-fact resolver
- **Spec:** §19.2 · **Priority:** Must · **Size:** M · **Depends on:** M2-13, M7-01
- **Acceptance criteria**
  - [ ] Descriptors (measure, scope, period, comparison, basis) evaluated through the finance engine or `truth.scenario_event` as `vc_eval`.
  - [ ] Regenerating data with a different seed does not break the golden set.

#### M7-03 Driver matcher
- **Spec:** §19.3 (driver matching) · **Priority:** Must · **Size:** M · **Depends on:** M7-02
- **Acceptance criteria**
  - [ ] `eval/matching.py` matches on company codes, accounts/brands, period overlap and allowed classification.
  - [ ] Unit tests for match, partial overlap and wrong classification.

#### M7-04 Metrics and gates
- **Spec:** §19.3 · **Priority:** Must · **Size:** M · **Depends on:** M7-03
- **Acceptance criteria**
  - [ ] All metrics in §19.3 computed; gate thresholds in config.
  - [ ] Exit code non-zero when any gate fails, with the failing metric and cases listed.

#### M7-05 Golden set authoring
- **Spec:** §19.2 coverage table · **Priority:** Must (25) / Should (47) · **Size:** L · **Depends on:** M7-01
- **Acceptance criteria**
  - [ ] ≥ 25 cases for v1.0, covering every category in §19.2 at least once and every use case UC-01..09.
  - [ ] 47 cases for Should, with an 80/20 dev/holdout split.

#### M7-06 Runner modes and recordings
- **Spec:** §19.5, §17 · **Priority:** Must · **Size:** M · **Depends on:** M3-03, M7-04
- **Acceptance criteria**
  - [ ] `vc eval run --mode replay|record|live [--tags …]`.
  - [ ] Replay run of the full dev set needs no network and finishes < 5 min in CI.

#### M7-07 Reports
- **Spec:** §19.6 · **Priority:** Must · **Size:** S · **Depends on:** M7-06
- **Acceptance criteria**
  - [ ] `eval/reports/<ts>/report.md` and `report.json` with per-case results, metrics, deltas vs `main`, cost and latency; line appended to `history.jsonl`.
  - [ ] `vc eval report <run>` re-renders a report.

#### M7-08 CI integration
- **Spec:** §27 steps 4–7 · **Priority:** Must · **Size:** S · **Depends on:** M7-07, M1-18
- **Acceptance criteria**
  - [ ] CI runs generator `ci` profile, replay eval with gates, contract snapshot check, leakage scan.
  - [ ] Eval summary posted as a PR comment.
  - [ ] Prompt changes without passing eval are blocked.

#### M7-09 Nightly live evaluation
- **Spec:** §19.5, §27 (`nightly-eval.yml`) · **Priority:** Should · **Size:** S · **Depends on:** M7-06
- **Acceptance criteria**
  - [ ] Live run including hold-out; alerts on gate failure, p95 latency > 30 s, or median cost +25% vs 7-day median.
  - [ ] Report artefact retained 90 days.

#### M7-10 LLM judge (non-gating)
- **Spec:** §19.4 · **Priority:** Could · **Size:** M · **Depends on:** M7-06
- **Acceptance criteria**
  - [ ] Judge from a different provider family scores clarity, soundness, caveats, POL-004 conformity (1–5).
  - [ ] Calibrated on 20 human-labelled answers; Cohen's kappa reported; never gates.

---

## E8 — Deployment and documentation (M8)

**Goal:** someone else can clone, run and understand the system; the AWS design is codified.

#### M8-01 README and quickstart
- **Spec:** §25, §28.1 M8 DoD · **Priority:** Must · **Size:** S · **Depends on:** M3-10
- **Acceptance criteria**
  - [ ] The §25 command sequence works from a clean clone (verified in a fresh directory).
  - [ ] README explains the four principles (§1), architecture diagram, and how to run evals.

#### M8-02 Architecture page, ADRs and runbooks
- **Spec:** §6.4, §23 · **Priority:** Must · **Size:** M
- **Acceptance criteria**
  - [ ] ADR-001..008 complete; ADRs added for OD-1..OD-6 as they are decided.
  - [ ] `docs/architecture.md` covers components, flows and data model; runbooks for seeding, re-embedding, audit verification.

#### M8-03 Demo script
- **Spec:** §28.2 · **Priority:** Should · **Size:** S · **Depends on:** M6-07
- **Acceptance criteria**
  - [ ] Scripted walkthrough: UC-01 question, version sensitivity (BUD26 vs FC226), causal trap, access denial, injection, commentary draft → approve, audit verify.

#### M8-04 CDK stacks
- **Spec:** §26, OD-6 · **Priority:** Could · **Size:** L · **Depends on:** M6-12
- **Acceptance criteria**
  - [ ] `NetworkStack`, `DataStack`, `AppStack`, `ObservabilityStack`; env selected via CDK context.
  - [ ] `cdk synth` passes in CI; no public DB endpoint; secrets from Secrets Manager.

#### M8-05 Release workflow
- **Spec:** §27 (`release.yml`) · **Priority:** Could · **Size:** S · **Depends on:** M8-04
- **Acceptance criteria**
  - [ ] On `v*` tags: build/push image, deploy to `dev`, manual approval for `stg`/`prod`, release notes with eval summary.

#### M8-06 Audit anchoring to WORM storage
- **Spec:** §15.2 · **Priority:** Could · **Size:** S · **Depends on:** M5-01, M8-04
- **Acceptance criteria**
  - [ ] Daily export of the head hash to an S3 bucket with Object Lock (compliance mode); verification compares against it.

---

## Traceability

### Use cases → tasks

| Use case | Delivered by | Verified by |
| --- | --- | --- |
| UC-01 Ask a variance question | M2-12, M2-13, M3-06, M3-07 | M3-10, M7-05 |
| UC-02 Generate commentary | M6-05 | M7-05 (commentary materiality coverage) |
| UC-03 Drill to evidence | M2-12 (`journal_lines`), M6-08 | M7-05 |
| UC-04 Review anomalies | M2-09, M6-08 | M7-05 (anomaly cases) |
| UC-05 Review and approve | M6-06, M6-07 | M6-11 |
| UC-06 Audit a run | M5-01, M5-02, M5-10 | M5-12 |
| UC-07 Respect access boundaries | M1-04, M1-06, M5-08 | M2-14, M7-05 |
| UC-08 Policy question | M4-01, M4-07, M4-08 | M7-05 (policy cases) |
| UC-09 Refuse unsafe requests | M5-06, M5-07 | M7-05 (refusal, injection) |

### Open decisions → tasks

| Decision | Resolved in |
| --- | --- |
| OD-1 Default model | M3-02 |
| OD-2 Embedding model | M4-06 |
| OD-3 Sync vs async questions | M6-04 |
| OD-4 LLM content in production audit | M5-10 |
| OD-5 Cross-encoder reranking | After M7 baseline (not scheduled) |
| OD-6 CDK vs Terraform | M8-04 |

### Not scheduled (post-v1, §28.4)

MCP server for the read-only tools · text-to-SQL over `rpt` views · streaming responses · reviewer UI · Entra ID On-Behalf-Of · journal partitioning and additional ledgers · cross-encoder reranking · LangGraph comparison implementation.
