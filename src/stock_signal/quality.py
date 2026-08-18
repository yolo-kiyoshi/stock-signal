from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stock_signal.domain.market_data import DailyBar


@dataclass(frozen=True, slots=True)
class CorporateActionGap:
    """調整済み系列に残った可能性がある株式分割・併合の段差。"""

    symbol: str
    previous_date: date
    trade_date: date
    price_ratio: Decimal
    raw_price_ratio: Decimal | None
    adjustment_factor: Decimal


def find_corporate_action_gaps(
    bars: Sequence[DailyBar],
    *,
    factor_tolerance: Decimal = Decimal("0.25"),
    basis_tolerance: Decimal = Decimal("0.03"),
) -> list[CorporateActionGap]:
    """調整係数に近い不連続が調整済み価格にも残っていないか検査する。

    調整係数が1以外の日だけを対象とし、通常の大幅な価格変動とは区別する。
    """
    by_instrument: dict[tuple[str, str], list[DailyBar]] = defaultdict(list)
    for bar in bars:
        if bar.is_adjusted:
            by_instrument[(bar.symbol, bar.provider)].append(bar)

    issues: list[CorporateActionGap] = []
    for instrument_bars in by_instrument.values():
        ordered = sorted(instrument_bars, key=lambda item: item.trade_date)
        for previous, current in zip(ordered, ordered[1:], strict=False):
            factor = current.adjustment_factor
            if factor is None or factor <= 0 or factor == Decimal("1"):
                continue
            ratio = current.open / previous.close
            candidates = {factor, Decimal("1") / factor}
            raw_ratio = (
                current.raw_open / previous.raw_close
                if current.raw_open is not None
                and previous.raw_close is not None
                and previous.raw_close > 0
                else None
            )
            if raw_ratio is not None:
                resembles_corporate_action = any(
                    candidate != Decimal("1")
                    and abs(raw_ratio - candidate) / candidate
                    <= factor_tolerance
                    for candidate in candidates
                )
                adjusted_still_matches_raw = (
                    abs(ratio - raw_ratio) / raw_ratio <= basis_tolerance
                )
                has_unadjusted_gap = (
                    resembles_corporate_action and adjusted_still_matches_raw
                )
            else:
                # raw値がない旧データでは、大きな分割・併合だけを保守的に検査する。
                material_candidates = {
                    candidate
                    for candidate in candidates
                    if candidate <= Decimal("0.75")
                    or candidate >= Decimal("1.3333333333")
                }
                has_unadjusted_gap = any(
                    abs(ratio - candidate) / candidate
                    <= Decimal("0.12")
                    for candidate in material_candidates
                )
            if has_unadjusted_gap:
                issues.append(
                    CorporateActionGap(
                        symbol=current.symbol,
                        previous_date=previous.trade_date,
                        trade_date=current.trade_date,
                        price_ratio=ratio,
                        raw_price_ratio=raw_ratio,
                        adjustment_factor=factor,
                    )
                )
    return issues
