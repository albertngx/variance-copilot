# ADR-013: Run a turn's tool calls one after another, in the model's order

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-22 |
| **Deciders** | Albert Navarro |
| **Supersedes** | None (amends SPEC §12.3: "execute up to max_parallel calls concurrently") |

## Context

SPEC §12.3 lets the agent loop execute up to four tool calls of one model turn concurrently through the run's MCP session. Two facts from the code built so far make that a poor fit:

- **The tool session already serialises calls.** `ToolSession.call` takes a lock and numbers each call (E2, `tools/session.py`), so concurrent requests through MCP wait for each other. Concurrency would add no speed.
- **Fact IDs come from call numbers** (`F<call>-<seq>`), and tool results, fact IDs included, go back to the model in the next request. With concurrent calls, the numbering would depend on scheduling. Then a replayed run (ADR-007) could send a request that differs from the recorded one, and CI would fail with a missing recording, at random.

## Decision

1. The loop executes a turn's tool calls one after another, in the order the model listed them, through the run's MCP session. Each still runs in its own RLS transaction.
2. At most four calls per turn are executed. A fifth or later call in the same turn gets an error result (`TOOL_LIMIT`) asking the model to call it again in its next turn.
3. The per-run limit of twelve executed tool calls is unchanged. Calls answered from the run's tool cache do not count towards it.

## Consequences

**Positive**

- Fact IDs and tool results are identical between a recording and its replay, so replayed CI runs are repeatable.
- The loop stays simple: no task group, no ordering of results.

**Negative / risks**

- A turn with several slow tool calls takes their sum, not their maximum. Tool queries are capped at ten seconds each and the run at ninety seconds, and the session lock already imposed this, so nothing changes in practice.
- If tools ever run on independent connections, concurrency could return with call numbers assigned in the model's order before execution. That would need a change to `ToolSession` and a new ADR.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Concurrent calls as §12.3 says | No speed gain behind the session lock, and nondeterministic fact numbering breaks replay |
| Concurrent calls with numbers reserved in model order | Changes the E2 tool session and its MCP contract for no measurable benefit today |
| One tool call per turn | Costs extra model turns, tokens and money for questions that need several facts at once |
