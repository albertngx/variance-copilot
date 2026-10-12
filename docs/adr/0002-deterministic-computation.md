# ADR-002: Compute deterministically; the LLM only explains

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

Language models make arithmetic and transcription errors. Finance users must be able to trust every number in an explanation and trace it back to journal lines (§2, §9).

## Decision

All numbers (variances, FX vs operational splits, bridges, materiality, anomalies) are computed by the deterministic finance engine (§9). The language model selects tools, interprets results and writes prose; it never performs arithmetic that reaches the user.

## Consequences

**Positive**

- Eliminates arithmetic hallucination.
- Every number is unit- and property-testable (§20).

**Negative / risks**

- The finance engine must cover every analysis users need; gaps cannot be filled by the model.
- More upfront engineering than prompting a model with raw data.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Let the model compute from raw data (code interpreter or in-context) | Non-deterministic, hard to test, error-prone with large tables |
