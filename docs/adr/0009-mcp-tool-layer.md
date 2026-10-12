# ADR-009: Expose the tool layer as an MCP server

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — (amends SPEC §6, §10, §12, §16, §18, §22–§24, §28) |

## Context

SPEC v1.0 defines a read-only tool layer (§10) that only our own agent loop can call, and lists "an MCP server exposing the read-only tools" as a *Could* item and a post-v1 roadmap entry (§28.3, §28.4).

By 2026 the Model Context Protocol (MCP) is the standard way to expose tools to AI clients: Claude, Copilot Studio, IDE agents and most agent frameworks speak it. Likely next steps for this product (a Teams client, Microsoft 365 Copilot or Claude access, other internal agents reusing the finance tools) all need the tools reachable over a standard protocol. Retrofitting that later means a second, divergent code path for tool exposure, auth and audit.

Forces:

- The tool layer is the product's core asset: deterministic finance analysis with facts, provenance and row-level security. It should be reusable beyond our own agent.
- Company scope must come only from verified identity (§13.6), never from tool arguments or client-supplied metadata.
- The fact ledger is run-scoped (§10.2); external clients have no "run" in our sense.
- External clients write their own final text, so our guardrails (§13) do not cover their answers.

## Decision

1. **The tool layer is an MCP server** (`variance_copilot.mcp`), built with the official MCP Python SDK, and is the **only** way tools are invoked — by external clients **and** by our own orchestrator.
2. **Our orchestrator is an MCP client.** By default it uses an in-memory transport in the same process (no network hop); it can switch to streamable HTTP when the tool server is deployed separately. One code path means external clients get exactly the behaviour our agent gets.
3. **Tool definitions are transport-agnostic.** Each tool is a plain function taking a typed Pydantic input and an explicit `ToolContext` (principal, company scope, roles, run ID, tool call ID). The MCP server adapts these to MCP tools; schemas are generated from the same Pydantic models.
4. **Structured results.** Tools declare an output schema and return structured content (rows, fact IDs, facts, effective scope, warnings), plus a short text summary for clients that ignore structured content. All tools carry the `readOnlyHint` annotation.
5. **Identity and scope come from the transport, never from the payload.**
   - In-process: the orchestrator opens one MCP session per run, bound server-side to the already-verified principal.
   - Remote (streamable HTTP at `/mcp`): the server is an OAuth 2.1 resource server per the MCP authorization spec, validating the same OIDC tokens as the REST API (§16.2); dev tokens in `dev`.
   - Client-supplied `_meta` or arguments can never set or widen company scope.
6. **Sessions are runs.** Each remote MCP session creates an `app.run` of kind `MCP_SESSION`; fact IDs, audit events (`TOOL_CALL` with MCP client name and version) and limits are scoped to it.
7. **Two tiers of exposure:**
   - **Analysis tools** (the §10.3 catalogue): raw facts for power users and other agents. Answers written by external clients from these are *not* guardrail-validated; the server instructions and tool descriptions say so.
   - **Validated answer tool** (`ask_variance_copilot`, *Should*): runs the full guarded Q&A agent and returns the rendered, validated answer with its run ID — the recommended path for external assistants that present results to people.
8. Policy documents MAY additionally be exposed as MCP resources (`doc://POL-005`) — *Could*.

## Consequences

**Positive**

- Teams, Copilot Studio, Claude and other agents can use the finance tools without new integration code.
- Contract tests exercise the same interface external clients use.
- MCP moves from post-v1 roadmap into v1 at low cost, because the server wraps tools we build anyway.

**Negative / risks**

- A small per-call overhead even in-process (serialisation through the MCP session). Acceptable against the 10-second tool budget; measured in M2-16.
- An additional dependency (MCP SDK) and an additional auth surface (`/mcp`). Mitigated by sharing the REST API's token validation and by security tests.
- External clients can rephrase or misstate numbers from analysis tools. Mitigated by the validated-answer tool, by clear tool descriptions, and by labelling: only answers returned with a Variance Copilot run ID and validation status count as validated.
- Tool descriptions become a public contract and a prompt surface; they are versioned and snapshotted like schemas.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Keep tools internal; add MCP after v1 (SPEC v1.0) | Two exposure paths later; integrations (Teams, Copilot) blocked on a retrofit |
| MCP server as a thin adapter, orchestrator calls tools directly | Cheaper per call, but external behaviour could drift from internal behaviour and would be tested less |
| Expose the REST API as an OpenAPI plugin only | Works for some clients, but MCP is the broader standard for agents and carries tool semantics (annotations, structured output) natively |
