from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DailyBar:
    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    provider: str
    is_adjusted: bool
    raw_open: Decimal | None = None
    raw_high: Decimal | None = None
    raw_low: Decimal | None = None
    raw_close: Decimal | None = None
    raw_volume: int | None = None
    adjustment_factor: Decimal | None = None


@dataclass(frozen=True, slots=True)
class SymbolMatch:
    symbol: str
    name: str
    market: str
    currency: str
    match_score: Decimal


@dataclass(frozen=True, slots=True)
class EarningsAnnouncement:
    """J-Quantsが提供する決算発表予定。"""

    symbol: str
    scheduled_date: date
    company_name: str
    fiscal_year: str | None = None
    fiscal_quarter: str | None = None


@dataclass(frozen=True, slots=True)
class ListedInstrument:
    """上場銘柄マスタの正規化済みレコード。"""

    symbol: str
    name: str
    english_name: str | None
    market: str
    sector_17_code: str | None
    sector_17_name: str | None
    sector_33_code: str | None
    sector_33_name: str | None
    instrument_type: str
    as_of_date: date


@dataclass(frozen=True, slots=True)
class BulkFile:
    """J-Quantsバルクダウンロード対象ファイル。"""

    key: str
    endpoint: str
    target_date: date
