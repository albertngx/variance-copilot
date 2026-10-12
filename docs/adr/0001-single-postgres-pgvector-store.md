# ADR-001: Use PostgreSQL with pgvector as the single data store

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

The system needs relational ledger data (§7), full-text and vector search over policy documents (§11), row-level security (§7.10) and an append-only audit trail (§15). Running a separate vector database or search engine would add operational surface and a second place where access control must be enforced.

## Decision

PostgreSQL 16 with the `pgvector` extension (HNSW indexes) holds all data: master data, journal and plan, semantic views, document chunks and embeddings, audit events, review state and evaluation ground truth, in separate schemas with separate roles (§7.2).

## Consequences

**Positive**

- One engine for relational data, full-text search, vectors, RLS and audit; one backup and one access model.
- Hybrid retrieval (BM25-style + vector) can be fused in a single SQL query.
- Managed offering available (Amazon RDS for PostgreSQL with `vector`).

**Negative / risks**

- Vector search performance is below dedicated engines at very large scale; acceptable for a corpus of policies and memos.
- Ties the stack to PostgreSQL features (RLS, `security_invoker` views).

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| PostgreSQL + dedicated vector DB (e.g. OpenSearch, Qdrant) | Second store, second access-control path, more operations |
| Data warehouse (e.g. Snowflake, BigQuery) | No RLS-by-transaction-setting pattern for tool calls; cost and local development friction |
