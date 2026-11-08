# ADR-011: Reject identity fields in tool arguments instead of ignoring them

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-08 |
| **Deciders** | Albert Navarro |
| **Supersedes** | None (amends SPEC §10.5 rule 3) |

## Context

SPEC §10.5 rule 3 says company scope, roles and run identifiers in tool arguments or request `_meta` "are ignored". The goal is that a client can never set or widen its own scope (ADR-009, decision 5).

M2-11 made every tool input model strict (`extra="forbid"`), so an argument such as `company_scope` is not ignored: the call fails with `INVALID_ARGUMENTS` before a transaction opens. Building the MCP server (M2-17) made the gap between the text and the code visible. `_meta` is different. MCP clients and SDKs put their own keys there (protocol version, client info), so failing on unknown `_meta` keys would break ordinary clients.

## Decision

1. Company scope, roles, run identifiers or any other field a tool does not declare are **rejected** in tool arguments: the call returns a structured `INVALID_ARGUMENTS` error and runs no query.
2. The same keys in request `_meta` are **ignored**. The server never reads `_meta` to decide who is calling. Identity comes only from the session the transport binds (in-process) or from the verified token (remote, M2-18).

## Consequences

**Positive**

- A client or model that tries to pass scope learns that it cannot, instead of silently getting a narrower or different result than it asked for.
- Strict inputs also catch misspelt argument names, which would otherwise fall back to defaults.

**Negative / risks**

- A new optional input field is a contract change for clients that sent it early. The contract snapshot (M2-15) makes every such change visible in review.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Ignore unknown argument fields (`extra="ignore"`), as the SPEC text said | Silent: a model that passes `company_codes` in the wrong place gets an unscoped answer without knowing it, and typos go unnoticed |
| Reject unknown `_meta` keys too | `_meta` carries SDK and protocol keys every client sends; rejecting them breaks standard clients |
