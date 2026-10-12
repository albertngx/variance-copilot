# ADR-007: Use recorded LLM responses in CI and live evaluation nightly

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

CI must be deterministic, fast and free, but regressions against real models must still be detected (§19).

## Decision

Pull-request CI runs the evaluation suite in `replay` mode: a `ReplayProvider` returns completions recorded in `eval/cassettes/`, keyed by a hash of the canonical request (§19.5). Developers re-record after prompt or tool changes. A nightly workflow runs `live` mode including hold-out cases.

## Consequences

**Positive**

- Deterministic, free CI with evaluation gates on every PR.
- Real-model drift is caught nightly.

**Negative / risks**

- Cassettes must be re-recorded when requests change; stale recordings fail loudly by design.
- Replay passing does not prove live behaviour; nightly results must be watched.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Live model calls in every PR | Cost, flakiness, provider outages block merges |
| Mock LLM responses written by hand | Unrealistic; does not exercise real model behaviour |
