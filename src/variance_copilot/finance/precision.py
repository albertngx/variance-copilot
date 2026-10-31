"""Shared Decimal context for finance arithmetic (SPEC §9.10)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from functools import wraps

PRECISION = 28
FINANCE_CONTEXT = Context(
    prec=PRECISION,
    rounding=ROUND_HALF_EVEN,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


def with_finance_context[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Run ``func`` in the shared 28-digit context, whatever the caller's context is."""

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(FINANCE_CONTEXT):
            return func(*args, **kwargs)

    return wrapper
