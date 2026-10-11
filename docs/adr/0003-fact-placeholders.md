# ADR-003: Insert facts through placeholders instead of model-typed numbers

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-10-12 |
| **Deciders** | Albert Navarro |
| **Supersedes** | — |

## Context

Even when numbers are computed deterministically, a model that copies them into prose can mistype, round inconsistently or invent figures (§13).

## Decision

Tool results register facts in a run-scoped fact ledger (§10.2), each with an ID. The model refers to numbers only through placeholders (e.g. `{{F3-2}}`); the renderer substitutes formatted values (§13.1). Any digit sequence in model text that is not a placeholder is a grounding violation (§13.2).

## Consequences

**Positive**

- Removes transcription errors; formatting is consistent.
- Validation becomes structural (does the placeholder exist and is it in scope?) rather than fuzzy matching.

**Negative / risks**

- Prompts and structured output must teach the model the placeholder convention; repair turns are needed when it slips.
- Derived numbers the model wants to mention must exist as facts.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| Post-hoc numeric matching of model text against tool output | Fuzzy, misses rounding and unit errors, hard to make strict |
