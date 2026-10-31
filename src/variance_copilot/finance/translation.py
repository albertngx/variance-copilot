"""Group-currency translation, constant currency and the FX split (SPEC §9.2)."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from variance_copilot.finance.codes import ErrorCode, FinanceError
from variance_copilot.finance.periods import Period
from variance_copilot.finance.precision import ONE, ZERO, with_finance_context
from variance_copilot.finance.versions import ResolvedVersion

GROUP_CURRENCY = "USD"


@dataclass(frozen=True)
class FxRate:
    """USD per unit of ``currency``, valid from ``valid_from`` until the next rate."""

    rate_type: str
    currency: str
    valid_from: date
    rate: Decimal


class FxRates:
    """Rates to USD by rate type and currency."""

    def __init__(self, rates: Iterable[FxRate]) -> None:
        series: dict[tuple[str, str], list[FxRate]] = defaultdict(list)
        for rate in rates:
            series[rate.rate_type, rate.currency].append(rate)
        self._series = {key: sorted(s, key=lambda r: r.valid_from) for key, s in series.items()}
        self._dates = {key: [r.valid_from for r in s] for key, s in self._series.items()}

    def rate(self, rate_type: str, currency: str, period: Period) -> Decimal:
        if currency == GROUP_CURRENCY:
            return ONE
        key = (rate_type, currency)
        index = bisect_right(self._dates.get(key, []), period.month_start) - 1
        if index < 0:
            raise FinanceError(
                ErrorCode.MISSING_FX_RATE, f"no {rate_type} rate for {currency} in {period}"
            )
        return self._series[key][index].rate


@dataclass(frozen=True)
class LocalAmount:
    """One period's amount in a local currency, with its group-currency value as booked."""

    period: Period
    currency: str
    amount_lc: Decimal
    amount_gc: Decimal


@with_finance_context
def constant_currency(
    amounts: Iterable[LocalAmount], comparison: ResolvedVersion, rates: FxRates
) -> Decimal:
    """``A_cc``: local amounts at the comparison's rates; USD amounts stay as booked."""
    total = ZERO
    for amount in amounts:
        if amount.currency == GROUP_CURRENCY:
            total += amount.amount_gc
        else:
            period = comparison.period_for(amount.period)
            total += amount.amount_lc * rates.rate(comparison.rate_type, amount.currency, period)
    return total


@dataclass(frozen=True)
class FxSplit:
    actual_gc: Decimal
    actual_cc: Decimal
    comparison_gc: Decimal
    delta_total: Decimal
    fx_effect: Decimal
    delta_operational: Decimal


@with_finance_context
def fx_split(
    amounts: Sequence[LocalAmount],
    comparison_gc: Decimal,
    comparison: ResolvedVersion,
    rates: FxRates,
) -> FxSplit:
    """Split ``A_gc - C_gc`` into FX (``A_gc - A_cc``) and operational (``A_cc - C_gc``)."""
    actual_gc = sum((a.amount_gc for a in amounts), ZERO)
    actual_cc = constant_currency(amounts, comparison, rates)
    return FxSplit(
        actual_gc=actual_gc,
        actual_cc=actual_cc,
        comparison_gc=comparison_gc,
        delta_total=actual_gc - comparison_gc,
        fx_effect=actual_gc - actual_cc,
        delta_operational=actual_cc - comparison_gc,
    )
