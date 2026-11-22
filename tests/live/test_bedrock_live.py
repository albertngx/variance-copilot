from __future__ import annotations

import json
import os

import boto3
import pytest

from variance_copilot.agent.providers.base import Completion, Message, ToolSpec
from variance_copilot.agent.providers.bedrock import BedrockProvider
from variance_copilot.agent.schemas import Answer
from variance_copilot.config import LLMProviderName, Settings

pytestmark = pytest.mark.skipif(
    os.environ.get("VC_LIVE_TESTS") != "1", reason="calls the real model; set VC_LIVE_TESTS=1"
)

TOOL = ToolSpec(
    "net_sales_variance",
    "Net sales variance facts for one company code, as fact IDs with values.",
    {
        "type": "object",
        "properties": {"company_code": {"type": "string", "description": "For example US10"}},
        "required": ["company_code"],
        "additionalProperties": False,
    },
)
SYSTEM = (
    "You explain FP&A variances. Always call net_sales_variance before answering. "
    "Never write numbers: refer to facts only with placeholders such as {{F1-1}}."
)
QUESTION = (
    "Why are US10 net sales below budget year to date? "
    "Scope: company code US10, FY2026 P09 YTD, actual vs BUD26, group currency, IFRS."
)
FACTS = {
    "facts": [
        {"id": "F1-1", "measure": "net_sales_variance", "value": "-41.2m USD"},
        {"id": "F1-2", "measure": "volume_effect", "value": "-35.0m USD", "brand": "CARDIVEX"},
        {"id": "F1-3", "measure": "gtn_prior_period", "value": "-6.2m USD"},
    ]
}


@pytest.fixture(scope="module")
def provider() -> BedrockProvider:
    settings = Settings()
    if settings.llm_provider is not LLMProviderName.BEDROCK or not settings.llm_model:
        pytest.skip("needs VC_LLM_PROVIDER=bedrock and VC_LLM_MODEL")
    if boto3.Session().get_credentials() is None:
        pytest.skip("no AWS credentials; run `aws login`")
    return BedrockProvider(settings.llm_model, region=settings.aws_region)


def ask(provider: BedrockProvider, messages: list[Message]) -> Completion:
    return provider.complete(
        messages,
        [TOOL],
        response_schema=Answer.model_json_schema(),
        temperature=0.0,
        max_output_tokens=2000,
        timeout_s=60.0,
    )


def test_a_tool_round_trip_ends_in_a_valid_answer(provider: BedrockProvider) -> None:
    messages = [Message("system", SYSTEM), Message("user", QUESTION)]

    first = ask(provider, messages)

    assert first.stop_reason == "tool_use"
    (call,) = first.message.tool_calls
    assert (call.name, call.arguments) == ("net_sales_variance", {"company_code": "US10"})

    messages += [first.message, Message("tool", json.dumps(FACTS), tool_call_id=call.call_id)]
    final = ask(provider, messages)

    assert final.stop_reason == "end_turn"
    Answer.model_validate_json(final.message.content)
    assert first.usage.input_tokens > 0
    assert final.usage.output_tokens > 0
