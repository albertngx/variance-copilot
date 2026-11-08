"""Structured output the model must return (SPEC §12.4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Classification = Literal[
    "VOLUME", "PRICE", "MIX", "GTN", "GTN_PRIOR_PERIOD", "FX", "LOSS_OF_EXCLUSIVITY",
    "LAUNCH", "TIMING", "ONE_OFF", "MISPOSTING", "RESTRUCTURING", "CORE_ADJUSTMENT",
    "INTERCOMPANY", "PERSONNEL", "OTHER",
]  # fmt: skip


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScopeEcho(_Output):
    company_codes: list[str]
    period_label: str
    comparison: str
    currency_basis: Literal["GC", "LC", "CC"]
    basis: Literal["IFRS", "CORE"]
    preliminary: bool = False


class Driver(_Output):
    title: str = Field(max_length=120)
    impact_fact_id: str
    classification: Classification
    narrative: str = Field(max_length=800, description="Text with {{fact}} placeholders only")
    recurrence: Literal["RECURRING", "NON_RECURRING", "TIMING", "UNKNOWN"]
    evidence_fact_ids: list[str] = []
    citation_ids: list[str] = []


class Answer(_Output):
    headline: str = Field(max_length=300)
    scope: ScopeEcho
    drivers: list[Driver] = Field(max_length=8)
    caveats: list[str] = []
    citation_ids: list[str] = []
    follow_up_questions: list[str] = Field(default=[], max_length=3)


class CommentarySection(_Output):
    section: Literal[
        "SUMMARY", "NET_SALES", "GROSS_MARGIN", "SELLING_MARKETING", "RESEARCH_DEVELOPMENT",
        "GENERAL_ADMIN", "OTHER_OPERATING", "CORE_RECONCILIATION", "OUTLOOK",
    ]  # fmt: skip
    headline: str
    drivers: list[Driver]


class Commentary(_Output):
    scope: ScopeEcho
    sections: list[CommentarySection]
    anomalies_to_review: list[str] = []


RESPONSE_MODELS: tuple[type[BaseModel], ...] = (Answer, Commentary)
