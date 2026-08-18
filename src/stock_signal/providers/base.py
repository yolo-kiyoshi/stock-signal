from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from stock_signal.domain.market_data import (
    BulkFile,
    DailyBar,
    EarningsAnnouncement,
    ListedInstrument,
    SymbolMatch,
)


class MarketDataError(RuntimeError):
    """市場データ取得元のエラー基底クラス。"""


class MarketDataAuthenticationError(MarketDataError):
    """API認証情報が未設定または拒否された場合に送出する。"""


class MarketDataRateLimitError(MarketDataError):
    """取得元のアクセス上限に到達した場合に送出する。"""


class MarketDataResponseError(MarketDataError):
    """取得元が不正または想定外の応答を返した場合に送出する。"""


class MarketDataTransportError(MarketDataError):
    """取得元へ接続できない場合に送出する。"""


class MarketDataProvider(ABC):
    """日足OHLCVを取得するための取得元非依存インターフェース。"""

    @abstractmethod
    def fetch_daily_prices(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        """取得可能な日足を取引日の昇順で返す。"""

    @abstractmethod
    def search_symbols(self, keywords: str) -> list[SymbolMatch]:
        """企業名または銘柄記号に一致する取得元固有銘柄を返す。"""


class LightPlanDataProvider(Protocol):
    """Lightプランの日次参照データを取得する契約。"""

    def fetch_topix_prices(
        self, start: date | None = None, end: date | None = None
    ) -> Sequence[DailyBar]: ...

    def fetch_earnings_calendar(self) -> Sequence[EarningsAnnouncement]: ...


class MarketUniverseProvider(Protocol):
    """全市場の銘柄マスタと日足バルクデータを取得する契約。"""

    def fetch_instrument_master(self, as_of: date) -> Sequence[ListedInstrument]: ...

    def list_bulk_files(
        self, endpoint: str, start: date, end: date
    ) -> Sequence[BulkFile]: ...

    def download_bulk_daily_bars(self, file_key: str) -> Sequence[DailyBar]: ...

    def fetch_market_daily_prices(
        self, start: date, end: date
    ) -> Sequence[DailyBar]: ...

    def fetch_daily_prices(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> Sequence[DailyBar]: ...
