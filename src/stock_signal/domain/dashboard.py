from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class WatchlistItem:
    symbol: str
    provider: str
    display_name: str
    exchange: str
    currency: str


@dataclass(frozen=True, slots=True)
class WatchlistRegistration:
    """画面から受け付けた証券コードの登録状態。"""

    symbol: str
    provider: str
    status: str
    display_name: str | None
    error_message: str | None
    requested_at: str
    updated_at: str
    watchlist_name: str = "ウォッチ"


@dataclass(frozen=True, slots=True)
class Instrument:
    """J-Quants銘柄マスタの画面利用項目。"""

    symbol: str
    provider: str
    display_name: str
    market: str
    sector_33_name: str | None
    instrument_type: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class WatchlistSummary:
    """ウォッチリスト名と登録銘柄数。"""

    name: str
    item_count: int


@dataclass(frozen=True, slots=True)
class Position:
    """保有銘柄の現在スナップショット。"""

    portfolio_name: str
    symbol: str
    provider: str
    display_name: str
    quantity: Decimal
    average_cost: Decimal | None
    account_type: str
    memo: str | None
    latest_close: Decimal | None = None
    latest_trade_date: str | None = None


@dataclass(frozen=True, slots=True)
class PredictionSummary:
    symbol: str
    display_name: str
    provider: str
    as_of_date: str
    horizon_days: int
    probability_up: float
    probability_flat: float
    probability_down: float
    predicted_class: str
    rank_score: float
    model_version_id: str


@dataclass(frozen=True, slots=True)
class TechnicalSignal:
    symbol: str
    display_name: str
    provider: str
    as_of_date: str
    direction: str
    strength: float
    last_close: float
    change_percent: float | None
    sma5: float
    sma20: float
    note: str
