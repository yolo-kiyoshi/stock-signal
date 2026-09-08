from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_DETAIL_ROWS = 5_000
_SYMBOL_PATTERN = re.compile(r"[0-9A-Z]{4}")
_REQUIRED_COLUMNS = (
    "種別",
    "銘柄コード・ティッカー",
    "銘柄",
    "口座",
    "保有数量",
    "平均取得価額",
)


class RakutenCsvError(ValueError):
    """楽天証券CSVを安全に解釈できない場合の利用者向けエラー。"""


@dataclass(frozen=True, slots=True)
class RakutenCsvIssue:
    """取り込めない国内株式行。"""

    row_number: int
    symbol: str | None
    message: str


@dataclass(frozen=True, slots=True)
class RakutenCsvPosition:
    """同一証券コード・複数口座を集約した保有残高。"""

    symbol: str
    display_name: str
    quantity: Decimal
    average_cost: Decimal | None
    account_types: tuple[str, ...]
    source_rows: int

    @property
    def account_type(self) -> str:
        if len(self.account_types) == 1:
            return self.account_types[0][:40]
        joined = f"複数口座（{'・'.join(self.account_types)}）"
        return joined if len(joined) <= 40 else "複数口座"


@dataclass(frozen=True, slots=True)
class RakutenCsvResult:
    """資産残高CSVの解析結果。元CSV自体は保存しない。"""

    positions: tuple[RakutenCsvPosition, ...]
    issues: tuple[RakutenCsvIssue, ...]
    skipped_types: dict[str, int]
    detail_rows: int
    domestic_rows: int
    duplicate_rows: int
    encoding: str


@dataclass(slots=True)
class _PositionAccumulator:
    display_name: str
    quantity: Decimal
    total_cost: Decimal
    cost_complete: bool
    account_types: list[str]
    source_rows: int


def _normalize(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _decode_csv(content: bytes) -> tuple[str, str]:
    if not content:
        raise RakutenCsvError("CSVファイルが空です")
    if len(content) > MAX_CSV_BYTES:
        raise RakutenCsvError("CSVファイルは2MB以下にしてください")
    if b"\x00" in content:
        raise RakutenCsvError("CSVファイルに解釈できない文字が含まれています")
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return content.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise RakutenCsvError("CSVの文字コードはUTF-8またはCP932にしてください")


def _decimal(value: str, label: str) -> Decimal:
    normalized = _normalize(value).replace(",", "")
    if not normalized or normalized == "-":
        raise RakutenCsvError(f"{label}が空です")
    try:
        result = Decimal(normalized)
    except InvalidOperation as error:
        raise RakutenCsvError(f"{label}を数値として解釈できません") from error
    if not result.is_finite() or result < 0:
        raise RakutenCsvError(f"{label}は0以上の有限値で指定してください")
    return result


def _optional_decimal(value: str, label: str) -> Decimal | None:
    normalized = _normalize(value)
    return None if not normalized or normalized == "-" else _decimal(normalized, label)


def parse_rakuten_asset_balance(content: bytes) -> RakutenCsvResult:
    """楽天証券の資産残高CSVから国内株式を証券コード単位で集約する。"""
    decoded, encoding = _decode_csv(content)
    try:
        rows = list(csv.reader(io.StringIO(decoded, newline="")))
    except csv.Error as error:
        raise RakutenCsvError("CSVの列構造を解釈できません") from error

    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if {_normalize(cell) for cell in row}.issuperset(_REQUIRED_COLUMNS)
        ),
        None,
    )
    if header_index is None:
        raise RakutenCsvError(
            "楽天証券の「保有商品詳細」ヘッダーが見つかりません。"
            "資産残高（すべて）のCSVを指定してください"
        )

    header = [_normalize(cell) for cell in rows[header_index]]
    indices = {column: header.index(column) for column in _REQUIRED_COLUMNS}
    maximum_index = max(indices.values())
    accumulators: dict[str, _PositionAccumulator] = {}
    issues: list[RakutenCsvIssue] = []
    skipped_types: Counter[str] = Counter()
    detail_rows = 0
    domestic_rows = 0

    for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not row or not any(cell.strip() for cell in row):
            continue
        first_cell = _normalize(row[0]) if row else ""
        if first_cell.startswith("■"):
            break
        detail_rows += 1
        if detail_rows > MAX_DETAIL_ROWS:
            raise RakutenCsvError("保有商品詳細が5,000行を超えています")
        if len(row) <= maximum_index:
            issues.append(RakutenCsvIssue(row_number, None, "必要な列が不足しています"))
            continue

        instrument_type = (_normalize(row[indices["種別"]]) or "種別不明")[:40]
        if instrument_type != "国内株式":
            skipped_types[instrument_type] += 1
            continue
        domestic_rows += 1
        raw_symbol = _normalize(row[indices["銘柄コード・ティッカー"]]).upper()
        symbol = raw_symbol if _SYMBOL_PATTERN.fullmatch(raw_symbol) else None
        if symbol is None:
            issues.append(
                RakutenCsvIssue(row_number, raw_symbol or None, "4文字の証券コードではありません")
            )
            continue

        try:
            quantity = _decimal(row[indices["保有数量"]], "保有数量")
            average_cost = _optional_decimal(row[indices["平均取得価額"]], "平均取得価額")
        except RakutenCsvError as error:
            issues.append(RakutenCsvIssue(row_number, symbol, str(error)))
            continue
        if quantity == 0:
            issues.append(RakutenCsvIssue(row_number, symbol, "保有数量が0のため対象外です"))
            continue

        display_name = (_normalize(row[indices["銘柄"]]) or symbol)[:200]
        account_type = (_normalize(row[indices["口座"]]) or "未設定")[:80]
        current = accumulators.get(symbol)
        if current is None:
            accumulators[symbol] = _PositionAccumulator(
                display_name=display_name,
                quantity=quantity,
                total_cost=quantity * average_cost if average_cost is not None else Decimal(),
                cost_complete=average_cost is not None,
                account_types=[account_type],
                source_rows=1,
            )
            continue
        current.quantity += quantity
        if average_cost is None:
            current.cost_complete = False
        else:
            current.total_cost += quantity * average_cost
        current.source_rows += 1
        if account_type not in current.account_types:
            current.account_types.append(account_type)

    if detail_rows == 0:
        raise RakutenCsvError("保有商品詳細にデータ行がありません")

    positions = []
    duplicate_rows = 0
    for symbol, current in sorted(accumulators.items()):
        duplicate_rows += current.source_rows - 1
        average_cost = None
        if current.cost_complete and current.quantity:
            average_cost = (current.total_cost / current.quantity).quantize(
                Decimal("0.000001"),
                rounding=ROUND_HALF_UP,
            )
        positions.append(
            RakutenCsvPosition(
                symbol=symbol,
                display_name=current.display_name,
                quantity=current.quantity,
                average_cost=average_cost,
                account_types=tuple(current.account_types),
                source_rows=current.source_rows,
            )
        )

    return RakutenCsvResult(
        positions=tuple(positions),
        issues=tuple(issues),
        skipped_types=dict(sorted(skipped_types.items())),
        detail_rows=detail_rows,
        domestic_rows=domestic_rows,
        duplicate_rows=duplicate_rows,
        encoding=encoding,
    )
