# ADR-004: Build our own agent loop behind a provider interface

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

The agent needs strict limits (turns, tool calls, cost, wall clock; §12.3), audit hooks on every call, replayable determinism (§12.6, §19.5) and portability across LLM providers (Bedrock, Azure OpenAI, OpenAI, Anthropic, Ollama).

## Decision

We implement a small agent loop (§12.3) over a provider interface (§12.2) with adapters per provider and a `ReplayProvider`. No agent framework is used for orchestration.

## Consequences

**Positive**

- Full control of limits, audit events and determinism.
- No framework lock-in; provider switch is configuration (`VC_LLM_PROVIDER`).

**Negative / risks**

- We maintain the loop, retries and structured-output handling ourselves.
- Framework features (tracing UIs, prebuilt patterns) must be added deliberately.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| LangChain / LangGraph, other agent frameworks | Abstractions hide limits and audit points; version churn; harder replay |
| Provider-native agent runtimes | Ties the product to one provider |
