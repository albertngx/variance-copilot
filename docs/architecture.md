# Architecture

High-level component view of Variance Copilot. [SPEC.md §6](../SPEC.md#6-architecture) is normative; the decisions behind it are in [adr/](adr/), and [design-docs/](../design-docs/) explain each part in depth.

## Components

```mermaid
flowchart LR
  subgraph Clients
    CLI["CLI (vc)"]
    APIC["API client"]
    MCPC["External MCP client<br/>(Teams bot, Claude, Copilot Studio, agents)"]
  end
  subgraph Service["Variance Copilot service (FastAPI)"]
    AUTH["AuthN / AuthZ<br/>(OIDC JWT, RBAC, company scope)"]
    ORCH["Orchestrator<br/>(Q&A agent loop, commentary pipeline)"]
    MCPS["MCP server<br/>(tool exposure, ADR-009)"]
    TOOLS["Tool layer<br/>(read-only, fact ledger)"]
    FIN["Finance engine<br/>(deterministic)"]
    RET["Retrieval<br/>(hybrid search)"]
    GUARD["Guardrails<br/>(grounding, direction, citations, scope)"]
    REND["Renderer<br/>(placeholder substitution)"]
    REV["Review workflow"]
    AUD["Audit writer<br/>(hash-chained)"]
  end
  subgraph Data["PostgreSQL 16 + pgvector"]
    MD[("md: master data")]
    FINDB[("fin: journal and plan")]
    RPT[("rpt: semantic views, RLS")]
    DOC[("doc: chunks and embeddings")]
    AUDDB[("audit: append-only events")]
    REVDB[("review: commentaries")]
    TRUTH[("truth: ground truth, eval only")]
  end
  LLM["LLM provider<br/>(Bedrock, Azure OpenAI, other)"]
  EMB["Embedding model"]
  CLI --> AUTH
  APIC --> AUTH
  AUTH --> ORCH
  ORCH <--> LLM
  MCPC --> AUTH
  AUTH --> MCPS
  ORCH -->|"MCP client"| MCPS
  MCPS --> TOOLS
  TOOLS --> FIN
  FIN --> RPT
  RPT --> FINDB
  RPT --> MD
  TOOLS --> RET
  RET --> DOC
  RET --> EMB
  ORCH --> GUARD
  GUARD --> REND
  REND --> REV
  REV --> REVDB
  ORCH --> AUD
  AUD --> AUDDB
```

## Key properties

| Property | How | Decision |
| --- | --- | --- |
| Numbers are never typed by the model | Deterministic finance engine; facts inserted through placeholders | [ADR-002](adr/0002-deterministic-computation.md), [ADR-003](adr/0003-fact-placeholders.md) |
| One tool interface for every client | Tool layer exposed as an MCP server; our orchestrator is an MCP client | [ADR-009](adr/0009-mcp-tool-layer.md) |
| Access control in the database | Row-level security by company code from the verified token | [ADR-006](adr/0006-rls-by-company-code.md) |
| Every run is reconstructable | Append-only, hash-chained audit events | [ADR-005](adr/0005-hash-chained-audit.md) |
| One data store | PostgreSQL 16 + pgvector, one schema per concern | [ADR-001](adr/0001-single-postgres-pgvector-store.md) |
| Provider-independent, replayable agent | Own loop behind a provider interface; recorded responses in CI | [ADR-004](adr/0004-own-agent-loop.md), [ADR-007](adr/0007-recorded-llm-ci.md) |

## Code map

| Package (`src/variance_copilot/`) | Component |
| --- | --- |
| `api/` | FastAPI service, AuthN/AuthZ |
| `agent/` | Orchestrator (Q&A loop, providers, renderer) |
| `mcp/` | MCP server and orchestrator client |
| `tools/` | Tool layer and fact ledger |
| `finance/` | Finance engine |
| `retrieval/` | Ingestion and hybrid search |
| `guardrails/` | Validators |
| `review/` | Commentary pipeline and workflow |
| `audit/` | Hash-chained audit writer and verification |
| `generator/` | Synthetic data (scaffolding) |
| `db/` | Engine, sessions, RLS context |
| `observability/` | Tracing, metrics, logging |
| `cli.py`, `config.py` | `vc` command line; settings and YAML config |
