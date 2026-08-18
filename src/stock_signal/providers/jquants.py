from __future__ import annotations

import csv
import gzip
import io
import re
import time
import zipfile
from calendar import monthrange
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_signal.domain.market_data import (
    BulkFile,
    DailyBar,
    EarningsAnnouncement,
    ListedInstrument,
    SymbolMatch,
)
from stock_signal.providers.base import (
    MarketDataAuthenticationError,
    MarketDataProvider,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTransportError,
)
from stock_signal.providers.http import (
    BinaryHttpClient,
    HttpClientError,
    JsonHttpClient,
    UrllibBinaryHttpClient,
    UrllibJsonHttpClient,
)


def _display_code(code: str) -> str:
    """普通株の5桁コードを一般的な4桁表示へ正規化する。"""
    normalized = code.strip().upper()
    return normalized[:4] if len(normalized) == 5 and normalized.endswith("0") else normalized


def _bulk_target_date(file_key: str, raw_date: str) -> date:
    """日次・月次バルクのキーから管理用の対象日を抽出する。"""
    for candidate in (raw_date, file_key):
        daily_match = re.search(
            r"(?<!\d)(20\d{2})[-/]?(\d{2})[-/]?(\d{2})(?!\d)",
            candidate,
        )
        if daily_match:
            year = int(daily_match[1])
            month = int(daily_match[2])
            day = int(daily_match[3])
            if 1 <= month <= 12 and 1 <= day <= monthrange(year, month)[1]:
                return date(year, month, day)
        monthly_match = re.search(
            r"(?<!\d)(20\d{2})[-/]?(\d{2})(?!\d)",
            candidate,
        )
        if monthly_match:
            year = int(monthly_match[1])
            month = int(monthly_match[2])
            if 1 <= month <= 12:
                return date(year, month, monthrange(year, month)[1])
    raise MarketDataResponseError(f"バルクファイルの日付を判定できません: {file_key}")


def _decode_bulk_file(content: bytes) -> str:
    """gzip、zip、または通常のCSVをUTF-8文字列へ変換する。"""
    if content.startswith(b"\x1f\x8b"):
        content = gzip.decompress(content)
    elif content.startswith(b"PK"):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise MarketDataResponseError(
                    "バルクzipにはCSVが1ファイルだけ含まれている必要があります"
                )
            content = archive.read(csv_names[0])
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise MarketDataResponseError(
            "J-QuantsのバルクCSVをUTF-8として読み取れません"
        ) from error


def _normalize_csv_column(column: str) -> str:
    """表記揺れを吸収するためCSV列名を英数字だけに正規化する。"""
    return re.sub(r"[^a-z0-9]", "", column.strip().casefold())


def _resolve_csv_column(
    columns: Mapping[str, str], aliases: tuple[str, ...]
) -> str | None:
    """候補名のうちCSVに存在する実列名を返す。"""
    for alias in aliases:
        resolved = columns.get(_normalize_csv_column(alias))
        if resolved is not None:
            return resolved
    return None


def _parse_bulk_trade_date(value: str) -> date:
    """ISO形式と区切りなし形式の取引日を受け入れる。"""
    normalized = value.strip()
    if re.fullmatch(r"\d{8}", normalized):
        return date(
            int(normalized[:4]),
            int(normalized[4:6]),
            int(normalized[6:8]),
        )
    return date.fromisoformat(normalized)


class JQuantsProvider(MarketDataProvider):
    """J-Quants API V2から調整済み日足を取得する。"""

    provider_name = "jquants"

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = "https://api.jquants.com/v2",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        minimum_request_interval: float = 12.1,
        http_client: JsonHttpClient | None = None,
        binary_http_client: BinaryHttpClient | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key.strip() if api_key else None
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._minimum_request_interval = minimum_request_interval
        self._http_client = http_client or UrllibJsonHttpClient()
        self._binary_http_client = binary_http_client or UrllibBinaryHttpClient()
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    def fetch_daily_prices(
        self,
        symbol: str,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        normalized_symbol = _display_code(symbol)
        if (
            not normalized_symbol.isascii()
            or not normalized_symbol.isalnum()
            or len(normalized_symbol) != 4
        ):
            raise ValueError(
                "J-Quantsの銘柄コードは4文字の半角英数字で指定してください"
            )
        if start and end and start > end:
            raise ValueError("開始日は終了日以前にしてください")
        params = {"code": normalized_symbol}
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()

        records = self._request_all("/equities/bars/daily", params)
        bars: list[DailyBar] = []
        for record in records:
            try:
                if not isinstance(record, Mapping):
                    raise TypeError("日足がオブジェクトではありません")
                if any(
                    record.get(field) is None
                    for field in ("AdjO", "AdjH", "AdjL", "AdjC")
                ):
                    continue
                trade_date = date.fromisoformat(str(record["Date"]))
                if start and trade_date < start:
                    continue
                if end and trade_date > end:
                    continue
                bars.append(
                    DailyBar(
                        symbol=normalized_symbol,
                        trade_date=trade_date,
                        open=Decimal(str(record["AdjO"])),
                        high=Decimal(str(record["AdjH"])),
                        low=Decimal(str(record["AdjL"])),
                        close=Decimal(str(record["AdjC"])),
                        volume=int(Decimal(str(record["AdjVo"]))),
                        provider=self.provider_name,
                        is_adjusted=True,
                        raw_open=(
                            Decimal(str(record["O"]))
                            if record.get("O") is not None
                            else None
                        ),
                        raw_high=(
                            Decimal(str(record["H"]))
                            if record.get("H") is not None
                            else None
                        ),
                        raw_low=(
                            Decimal(str(record["L"]))
                            if record.get("L") is not None
                            else None
                        ),
                        raw_close=(
                            Decimal(str(record["C"]))
                            if record.get("C") is not None
                            else None
                        ),
                        raw_volume=(
                            int(Decimal(str(record["Vo"])))
                            if record.get("Vo") is not None
                            else None
                        ),
                        adjustment_factor=(
                            Decimal(str(record["AdjFactor"]))
                            if record.get("AdjFactor") is not None
                            else None
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    f"J-Quantsの日足データが不正です: {record.get('Date', '日付不明')}"
                ) from error
        return sorted(bars, key=lambda bar: bar.trade_date)

    def fetch_market_daily_prices(self, start: date, end: date) -> list[DailyBar]:
        """指定期間を日付単位に分け、全市場の調整済み日足を取得する。"""
        if start > end:
            raise ValueError("開始日は終了日以前にしてください")
        records: list[Mapping[str, Any]] = []
        target_date = start
        while target_date <= end:
            # 全銘柄取得ではdateが必須。土日は明らかに取引がないため呼び出さない。
            if target_date.weekday() < 5:
                records.extend(
                    self._request_all(
                        "/equities/bars/daily",
                        {"date": target_date.isoformat()},
                    )
                )
            target_date += timedelta(days=1)
        bars: list[DailyBar] = []
        for record in records:
            try:
                if not isinstance(record, Mapping):
                    raise TypeError("日足がオブジェクトではありません")
                required = ("Date", "Code", "AdjO", "AdjH", "AdjL", "AdjC")
                if any(record.get(field) is None for field in required):
                    continue
                trade_date = date.fromisoformat(str(record["Date"]))
                if not start <= trade_date <= end:
                    continue
                bars.append(
                    DailyBar(
                        symbol=_display_code(str(record["Code"])),
                        trade_date=trade_date,
                        open=Decimal(str(record["AdjO"])),
                        high=Decimal(str(record["AdjH"])),
                        low=Decimal(str(record["AdjL"])),
                        close=Decimal(str(record["AdjC"])),
                        volume=int(Decimal(str(record.get("AdjVo") or 0))),
                        provider=self.provider_name,
                        is_adjusted=True,
                        raw_open=(
                            Decimal(str(record["O"]))
                            if record.get("O") is not None
                            else None
                        ),
                        raw_high=(
                            Decimal(str(record["H"]))
                            if record.get("H") is not None
                            else None
                        ),
                        raw_low=(
                            Decimal(str(record["L"]))
                            if record.get("L") is not None
                            else None
                        ),
                        raw_close=(
                            Decimal(str(record["C"]))
                            if record.get("C") is not None
                            else None
                        ),
                        raw_volume=(
                            int(Decimal(str(record["Vo"])))
                            if record.get("Vo") is not None
                            else None
                        ),
                        adjustment_factor=(
                            Decimal(str(record["AdjFactor"]))
                            if record.get("AdjFactor") is not None
                            else None
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    "J-Quantsの全市場日足データが不正です: "
                    f"{record.get('Date', '日付不明')}"
                ) from error
        return sorted(bars, key=lambda bar: (bar.trade_date, bar.symbol))

    def search_symbols(self, keywords: str) -> list[SymbolMatch]:
        query = keywords.strip()
        if not query:
            raise ValueError("検索語を指定してください")
        code_query = query.upper()
        params = (
            {"code": code_query}
            if len(code_query) in {4, 5}
            and code_query.isascii()
            and code_query.isalnum()
            else {}
        )
        records = self._request_all("/equities/master", params)
        lowered = query.casefold()
        matches = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            code = _display_code(str(record.get("Code", "")))
            name = str(record.get("CoName", ""))
            english_name = str(record.get("CoNameEn", ""))
            if lowered not in f"{code} {name} {english_name}".casefold():
                continue
            matches.append(
                SymbolMatch(
                    symbol=code,
                    name=name,
                    market=str(record.get("MktNm", "東京証券取引所")),
                    currency="JPY",
                    match_score=Decimal("1") if lowered == code.casefold() else Decimal("0.8"),
                )
            )
        return matches[:20]

    def fetch_instrument_master(self, as_of: date) -> list[ListedInstrument]:
        """指定日時点の全上場銘柄マスタを取得する。"""
        records = self._request_all(
            "/equities/master",
            {"date": as_of.isoformat()},
        )
        items = []
        for record in records:
            try:
                code = _display_code(str(record["Code"]))
                name = str(record["CoName"])
                items.append(
                    ListedInstrument(
                        symbol=code,
                        name=name,
                        english_name=(
                            str(record["CoNameEn"])
                            if record.get("CoNameEn")
                            else None
                        ),
                        market=str(record.get("MktNm") or "東京証券取引所"),
                        sector_17_code=(
                            str(record["S17"])
                            if record.get("S17") is not None
                            else None
                        ),
                        sector_17_name=(
                            str(record["S17Nm"])
                            if record.get("S17Nm")
                            else None
                        ),
                        sector_33_code=(
                            str(record["S33"])
                            if record.get("S33") is not None
                            else None
                        ),
                        sector_33_name=(
                            str(record["S33Nm"])
                            if record.get("S33Nm")
                            else None
                        ),
                        instrument_type=str(record.get("Type") or "stock"),
                        as_of_date=as_of,
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise MarketDataResponseError(
                    f"J-Quantsの銘柄マスタが不正です: {record}"
                ) from error
        return sorted(items, key=lambda item: item.symbol)

    def list_bulk_files(
        self,
        endpoint: str,
        start: date,
        end: date,
    ) -> list[BulkFile]:
        """Light以上で取得できるバルクファイル一覧を返す。"""
        if start > end:
            raise ValueError("開始日は終了日以前にしてください")
        records = self._request_all(
            "/bulk/list",
            {
                "endpoint": endpoint,
                "from": start.isoformat(),
                "to": end.isoformat(),
            },
        )
        files = []
        for record in records:
            key = str(record.get("Key") or record.get("key") or "")
            if not key:
                raise MarketDataResponseError("バルクファイルにkeyがありません")
            raw_date = str(record.get("Date") or record.get("date") or "")
            target_date = _bulk_target_date(key, raw_date)
            files.append(
                BulkFile(
                    key=key,
                    endpoint=str(record.get("Endpoint") or record.get("endpoint") or endpoint),
                    target_date=target_date,
                )
            )
        return sorted(files, key=lambda item: (item.target_date, item.key))

    def download_bulk_daily_bars(self, file_key: str) -> list[DailyBar]:
        """署名付きURLからCSVを取得して調整済み日足へ変換する。"""
        payload = self._request("/bulk/get", {"key": file_key})
        url = payload.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise MarketDataResponseError("バルク取得URLが不正です")
        try:
            content = self._binary_http_client.get_bytes(
                url,
                max(self._timeout_seconds, 300.0),
            )
        except HttpClientError as error:
            raise MarketDataTransportError(str(error)) from error
        decoded = _decode_bulk_file(content)
        try:
            dialect = csv.Sniffer().sniff(decoded[:8192], delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel
        rows = csv.DictReader(io.StringIO(decoded), dialect=dialect)
        if not rows.fieldnames:
            raise MarketDataResponseError(
                "J-QuantsのバルクCSVにヘッダーがありません"
            )
        columns = {
            _normalize_csv_column(column): column
            for column in rows.fieldnames
            if column is not None
        }
        code_column = _resolve_csv_column(
            columns,
            ("Code", "LocalCode", "IssueCode", "SecurityCode"),
        )
        date_column = _resolve_csv_column(columns, ("Date", "TradeDate"))
        adjusted_columns = {
            "open": _resolve_csv_column(
                columns, ("AdjO", "AdjOpen", "AdjustmentOpen", "AdjustedOpen")
            ),
            "high": _resolve_csv_column(
                columns, ("AdjH", "AdjHigh", "AdjustmentHigh", "AdjustedHigh")
            ),
            "low": _resolve_csv_column(
                columns, ("AdjL", "AdjLow", "AdjustmentLow", "AdjustedLow")
            ),
            "close": _resolve_csv_column(
                columns, ("AdjC", "AdjClose", "AdjustmentClose", "AdjustedClose")
            ),
            "volume": _resolve_csv_column(
                columns,
                ("AdjVo", "AdjVolume", "AdjustmentVolume", "AdjustedVolume"),
            ),
        }
        raw_columns = {
            "open": _resolve_csv_column(columns, ("O", "Open")),
            "high": _resolve_csv_column(columns, ("H", "High")),
            "low": _resolve_csv_column(columns, ("L", "Low")),
            "close": _resolve_csv_column(columns, ("C", "Close")),
            "volume": _resolve_csv_column(columns, ("Vo", "Volume")),
        }
        factor_column = _resolve_csv_column(
            columns,
            ("AdjFactor", "AdjustmentFactor", "Factor"),
        )
        has_adjusted_ohlc = all(
            adjusted_columns[key] for key in ("open", "high", "low", "close")
        )
        has_raw_ohlc = all(raw_columns[key] for key in ("open", "high", "low", "close"))
        if (
            code_column is None
            or date_column is None
            or not (has_adjusted_ohlc or has_raw_ohlc)
        ):
            actual_columns = ", ".join(rows.fieldnames)
            raise MarketDataResponseError(
                "J-QuantsのバルクCSVに日足の必須列がありません: "
                f"{actual_columns}"
            )
        bars = []
        source_rows = 0
        for row in rows:
            source_rows += 1
            try:
                price_columns = adjusted_columns if has_adjusted_ohlc else raw_columns
                required_columns = (
                    code_column,
                    date_column,
                    price_columns["open"],
                    price_columns["high"],
                    price_columns["low"],
                    price_columns["close"],
                )
                if any(column is None or not row.get(column) for column in required_columns):
                    continue
                volume_column = price_columns["volume"] or raw_columns["volume"]
                raw_volume_column = raw_columns["volume"]
                bars.append(
                    DailyBar(
                        symbol=_display_code(str(row[code_column])),
                        trade_date=_parse_bulk_trade_date(str(row[date_column])),
                        open=Decimal(str(row[price_columns["open"]])),
                        high=Decimal(str(row[price_columns["high"]])),
                        low=Decimal(str(row[price_columns["low"]])),
                        close=Decimal(str(row[price_columns["close"]])),
                        volume=int(
                            Decimal(str(row.get(volume_column) or 0))
                            if volume_column
                            else 0
                        ),
                        provider=self.provider_name,
                        is_adjusted=has_adjusted_ohlc,
                        raw_open=(
                            Decimal(str(row[raw_columns["open"]]))
                            if raw_columns["open"] and row.get(raw_columns["open"])
                            else None
                        ),
                        raw_high=(
                            Decimal(str(row[raw_columns["high"]]))
                            if raw_columns["high"] and row.get(raw_columns["high"])
                            else None
                        ),
                        raw_low=(
                            Decimal(str(row[raw_columns["low"]]))
                            if raw_columns["low"] and row.get(raw_columns["low"])
                            else None
                        ),
                        raw_close=(
                            Decimal(str(row[raw_columns["close"]]))
                            if raw_columns["close"] and row.get(raw_columns["close"])
                            else None
                        ),
                        raw_volume=(
                            int(Decimal(str(row[raw_volume_column])))
                            if raw_volume_column and row.get(raw_volume_column)
                            else None
                        ),
                        adjustment_factor=(
                            Decimal(str(row[factor_column]))
                            if factor_column and row.get(factor_column)
                            else None
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    "J-Quantsのバルク日足が不正です: "
                    f"{row.get(date_column, '日付不明')}"
                ) from error
        if not bars:
            raise MarketDataResponseError(
                "J-QuantsのバルクCSVから有効な日足を読み取れませんでした"
                f"（CSV行数: {source_rows}）"
            )
        return sorted(bars, key=lambda bar: (bar.trade_date, bar.symbol))

    def fetch_topix_prices(
        self,
        start: date | None = None,
        end: date | None = None,
    ) -> list[DailyBar]:
        """Lightプランで利用可能なTOPIX日足を取得する。"""
        if start and end and start > end:
            raise ValueError("開始日は終了日以前にしてください")
        params = {}
        if start:
            params["from"] = start.isoformat()
        if end:
            params["to"] = end.isoformat()
        records = self._request_all("/indices/bars/daily/topix", params)
        bars = []
        for record in records:
            try:
                if not isinstance(record, Mapping):
                    raise TypeError("TOPIX日足がオブジェクトではありません")
                trade_date = date.fromisoformat(str(record["Date"]))
                bars.append(DailyBar(
                    symbol="TOPIX",
                    trade_date=trade_date,
                    open=Decimal(str(record["O"])),
                    high=Decimal(str(record["H"])),
                    low=Decimal(str(record["L"])),
                    close=Decimal(str(record["C"])),
                    volume=0,
                    provider=self.provider_name,
                    is_adjusted=False,
                ))
            except (KeyError, TypeError, ValueError, InvalidOperation) as error:
                raise MarketDataResponseError(
                    f"J-QuantsのTOPIX日足が不正です: {record.get('Date', '日付不明')}"
                ) from error
        return sorted(bars, key=lambda bar: bar.trade_date)

    def fetch_earnings_calendar(self) -> list[EarningsAnnouncement]:
        """全プランで利用可能な決算発表予定日を取得する。"""
        records = self._request_all("/equities/earnings-calendar", {})
        announcements = []
        for record in records:
            try:
                if not isinstance(record, Mapping):
                    raise TypeError("決算予定がオブジェクトではありません")
                symbol = _display_code(str(record["Code"]))
                announcements.append(EarningsAnnouncement(
                    symbol=symbol,
                    scheduled_date=date.fromisoformat(str(record["Date"])),
                    company_name=str(record.get("CoName", symbol)),
                    fiscal_year=str(record["FY"]) if record.get("FY") else None,
                    fiscal_quarter=str(record["FQ"]) if record.get("FQ") else None,
                ))
            except (KeyError, TypeError, ValueError) as error:
                raise MarketDataResponseError(
                    f"J-Quantsの決算予定データが不正です: {record}"
                ) from error
        return sorted(
            announcements, key=lambda item: (item.scheduled_date, item.symbol)
        )

    def _request_all(self, path: str, params: Mapping[str, str]) -> list[Mapping[str, Any]]:
        query = dict(params)
        records: list[Mapping[str, Any]] = []
        while True:
            payload = self._request(path, query)
            batch = payload.get("data")
            if not isinstance(batch, list):
                raise MarketDataResponseError("J-Quantsの応答にdata配列がありません")
            records.extend(batch)
            pagination_key = payload.get("pagination_key")
            if not pagination_key:
                return records
            query["pagination_key"] = str(pagination_key)

    def _request(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        if not self._api_key:
            raise MarketDataAuthenticationError(
                "JQUANTS_API_KEYを.envに設定してください"
            )
        for attempt in range(self._max_retries + 1):
            self._wait_for_rate_limit()
            try:
                payload = self._http_client.get_json(
                    f"{self._base_url}{path}",
                    params,
                    self._timeout_seconds,
                    headers={"x-api-key": self._api_key},
                )
                self._last_request_at = self._monotonic()
            except HttpClientError as error:
                self._last_request_at = self._monotonic()
                if error.status_code == 401 or error.status_code == 403:
                    raise MarketDataAuthenticationError(
                        "J-QuantsがAPIキーまたはプランを拒否しました"
                    ) from error
                if error.status_code == 429:
                    if attempt < self._max_retries:
                        self._sleep(60)
                        continue
                    raise MarketDataRateLimitError(
                        "J-Quantsのアクセス上限に達しました"
                    ) from error
                if (
                    error.status_code is not None
                    and error.status_code >= 500
                    and attempt < self._max_retries
                ):
                    self._sleep(2**attempt)
                    continue
                if (
                    error.status_code is not None
                    and 400 <= error.status_code < 500
                ):
                    raise MarketDataResponseError(
                        f"J-Quantsがリクエストを拒否しました: {error}"
                    ) from error
                raise MarketDataTransportError(str(error)) from error
            if not isinstance(payload, Mapping):
                raise MarketDataResponseError(
                    "J-QuantsがJSONオブジェクト以外を返しました"
                )
            if payload.get("message") and "data" not in payload:
                raise MarketDataResponseError(f"J-Quants取得エラー: {payload['message']}")
            return payload
        raise AssertionError("再試行処理が予期せず終了しました")

    def _wait_for_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._monotonic() - self._last_request_at
        wait_seconds = self._minimum_request_interval - elapsed
        if wait_seconds > 0:
            self._sleep(wait_seconds)
