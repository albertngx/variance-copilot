# ADR-006: Enforce row-level security by company code

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

Users may only see data for their assigned company codes (§3). Relying on prompts or application filters risks cross-entity leakage through bugs or prompt injection (§18.1).

## Decision

PostgreSQL row-level security policies on the semantic views filter by a transaction-local setting holding the caller's company scope, derived only from the verified identity (§7.10, §13.6). Missing settings deny by default; views use `security_invoker = true`.

## Consequences

**Positive**

- Access control is enforced by the database, independent of prompts and tool code.
- Safe with connection pooling (transaction-local settings).

**Negative / risks**

- Every tool query must run in a transaction that sets the scope.
- RLS bypass attempts need dedicated security tests (§20).

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Application-level WHERE filters | One missed filter leaks data |
| Separate database per company | Breaks group-level analysis and multiplies operations |
