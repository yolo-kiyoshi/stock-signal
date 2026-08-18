from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatchItemResult:
    """銘柄単位の日次処理結果。"""

    symbol: str
    provider: str
    status: str
    received: int = 0
    upserted: int = 0
    first_date: str | None = None
    last_date: str | None = None
    error_message: str | None = None
    analysis_summary: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class BatchResult:
    """日次バッチ全体の結果。"""

    run_id: str
    status: str
    items: tuple[BatchItemResult, ...]

    @property
    def succeeded(self) -> int:
        return sum(item.status in {"success", "no_updates"} for item in self.items)

    @property
    def failed(self) -> int:
        return sum(item.status == "failed" for item in self.items)
