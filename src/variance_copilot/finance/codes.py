"""Warning and error codes shared by the finance engine and the tool layer (SPEC §10.1)."""

from __future__ import annotations

from enum import StrEnum


class WarningCode(StrEnum):
    OPEN_PERIOD = "OPEN_PERIOD"
    PARTIAL_SCOPE = "PARTIAL_SCOPE"
    NO_COMPARISON_BASE = "NO_COMPARISON_BASE"
    TRUNCATED = "TRUNCATED"
    NO_DATA = "NO_DATA"


class ErrorCode(StrEnum):
    INVALID_PERIOD = "INVALID_PERIOD"
    UNKNOWN_VERSION = "UNKNOWN_VERSION"
    VERSION_PERIOD_MISMATCH = "VERSION_PERIOD_MISMATCH"
    NO_FORECAST = "NO_FORECAST"
    MISSING_FX_RATE = "MISSING_FX_RATE"


class FinanceError(Exception):
    """A request the engine cannot answer; the tool layer returns it as ``error``."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
