# ADR-012: Amazon Bedrock with Claude Haiku 5.5 as the default model

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-08 |
| **Deciders** | Albert Navarro |
| **Supersedes** | None (settles OD-1; amends SPEC §12.6 and the `VC_LLM_PROVIDER` default in §24) |

## Context

OD-1 (SPEC §30) asks which model development, recording and nightly runs use. The default until decided was "provider with an available key; Bedrock for the AWS deployment", and the configured default was `openai`.

Variance Copilot is a proof of concept. Two constraints drive the choice:

- **Low cost.** CI replays recordings and never calls a model (ADR-007), so money is spent only on recording cassettes, on questions asked during development and on nightly runs. A Q&A run is roughly 5 turns of about 10k input tokens each (the whole conversation is resent every turn) and about 2k output tokens in total.
- **AWS-based.** The target deployment is AWS (§24), and one cloud account keeps billing, credentials and access control in one place.

The provider must support tool use, a JSON schema for the final answer, token usage and stop reasons (M3-02).

Prices at the time of writing, per million tokens (input / output), on-demand on Bedrock:

| Model | Price | Per Q&A run (about 50k in, 2k out) |
| --- | --- | --- |
| Claude Haiku 5.5 | 0.10 / 0.50 global routing; about 0.11 / 0.55 regional; prompts under 100k tokens | about USD 0.007 |
| Claude Sonnet 5.5 | about 2 / 10 | about USD 0.12 |
| Amazon Nova Lite | 0.06 / 0.24 | about USD 0.004 |

## Decision

1. **Provider:** Amazon Bedrock through the Converse API (`boto3`), as SPEC §12.2 already names for `BedrockProvider`. `VC_LLM_PROVIDER` defaults to `bedrock`; the region is `VC_AWS_REGION` (default `eu-central-1`).
2. **Model:** Claude Haiku 5.5 for development, recording and nightly runs. `VC_LLM_MODEL` pins its Bedrock model or inference-profile ID, chosen in M3-02 when model access is enabled, and `config/model_prices.yaml` gets the matching rate. The data is synthetic, so a global inference profile is acceptable for development.
3. **Step up by evidence:** if Haiku 5.5 cannot answer the thin-slice golden cases (M3-10) with valid placeholders after the repair turn, switch to Claude Sonnet 5.5 and update this ADR. Switching is a change of `VC_LLM_MODEL` plus a re-recording.
4. **Sampling parameters:** the loop keeps requesting temperature 0, and the value stays in the replay key (§19.5). A live provider omits sampling parameters the pinned model rejects; Claude Haiku 5.5 accepts no temperature or `top_p` other than its defaults, so the Bedrock provider sends none for it. Repeatable CI comes from replay, not from temperature.
5. **No extended reasoning:** the Bedrock provider turns the model's reasoning mode off. It costs output tokens, and its blocks would have to be sent back on every turn, which the §12.2 `Message` does not carry. Revisit only if answer quality needs it.

## Consequences

**Positive**

- One AWS account for the model, and later for embeddings (OD-2) and deployment; no separate vendor key.
- Very low cost: a full re-recording of 25 golden cases is well under one US dollar, and CI costs nothing.
- Converse uses one request shape for every Bedrock model, so trying Nova or Sonnet later needs no new provider code.
- Bedrock structured output requires `additionalProperties: false` on every object; the M3-04 output schemas already have it (`extra="forbid"`).

**Negative / risks**

- Needs an AWS account with Bedrock model access (a one-time use-case form for Anthropic models) and local AWS credentials. The live smoke test (M3-02) and the first recordings (M3-10) wait for that; nothing else does.
- A small model may follow the "never type digits" rule less reliably. The numeric grounding validator and repair turn (M5-03, M5-09), the evaluation gates (M7) and the Sonnet step-up cover this.
- Live runs are not repeatable, because temperature is not sent. CI is unaffected (replay), and nightly runs already use tolerant matching (§19).
- The rate depends on routing (global or regional) and on each request staying under 100k prompt tokens. The price table must use the rate of the pinned ID, and the §12.3 cost cap bounds a run either way.
- Whether structured output and tools can be combined in one Converse request is confirmed by the M3-02 smoke test. If not, the provider offers the answer schema as a final "submit answer" tool instead.

## Alternatives considered

| Alternative | Why not |
| --- | --- |
| OpenAI (the previous configured default) | Not AWS-based; a second vendor account and key |
| Anthropic API directly, same model | Same model and price, but billed and keyed outside AWS |
| Bedrock through the Anthropic SDK's Bedrock client | Claude only; Converse keeps other Bedrock models one setting away and is what §12.2 specifies |
| Amazon Nova Lite or Micro | Saves fractions of a cent per run but is more likely to break the placeholder rules; can still be tried later through `VC_LLM_MODEL` |
| Claude Sonnet 5.5 as the default | About 17 times the cost of Haiku 5.5; kept as the step-up in decision 3 |
| Ollama (local) | Free, but local models are weaker at multi-step tool use and the strict output rules |
