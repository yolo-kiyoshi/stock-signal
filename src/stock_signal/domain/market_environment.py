from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class MarketRegime(StrEnum):
    """寄り付き前に観測した外部市場の警戒区分。"""

    NORMAL = "normal"
    CAUTION = "caution"
    SEVERE = "severe"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """取得元から得た一つの市場指標と直前値。"""

    indicator_key: str
    label: str
    observation_date: date
    value: float
    previous_value: float | None
    unit: str
    source: str

    @property
    def change_value(self) -> float | None:
        if self.previous_value is None:
            return None
        return self.value - self.previous_value

    @property
    def change_percent(self) -> float | None:
        if self.previous_value in {None, 0}:
            return None
        return (self.value / self.previous_value - 1) * 100


@dataclass(frozen=True, slots=True)
class MarketRiskComponent:
    """複数の観測値を一つの説明可能なリスク要因へ集約した結果。"""

    key: str
    label: str
    score: float
    level: MarketRegime
    description: str
    indicator_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketRegimeSnapshot:
    """特定の判断時刻より前に利用できた情報だけで作る市場環境判定。"""

    decision_date: date
    decision_at: datetime
    regime: MarketRegime
    risk_score: float
    coverage_ratio: float
    components: tuple[MarketRiskComponent, ...]
    reasons: tuple[str, ...]
    cautions: tuple[str, ...]
    observations: tuple[MarketObservation, ...]
    engine_version: str = "market-regime-v1.0.0"

