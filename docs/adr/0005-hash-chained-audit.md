# ADR-005: Store audit events append-only and hash-chained

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

Explanations and commentary feed financial reporting, so internal control requires that every run can be reconstructed and that tampering with the trail is detectable (§15).

## Decision

Audit events are inserted into an append-only table; triggers and grants prevent updates and deletes. Each event stores `row_hash = SHA-256(prev_hash ‖ JCS(event))` forming a global chain (§15.2). `vc audit verify` recomputes the chain; in AWS the head hash is anchored daily to S3 Object Lock.

## Consequences

**Positive**

- Tamper evidence suitable for internal control and external audit.
- Verification is a simple, offline recomputation.

**Negative / risks**

- Inserts are serialised (advisory lock), limiting audit write throughput.
- Truncate-and-rebuild is only detectable with external anchoring.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Plain audit table | No tamper evidence |
| External ledger / blockchain service | Operational and cost overhead for no extra assurance at this scale |
