"""財務・イベント・資金制約を価格方向とは別に統合する。"""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from functools import cached_property

from stock_signal.database import get_instruments_by_symbols, list_positions, load_daily_bars
from stock_signal.domain.analysis import AnalysisResult, EntryStage, InvestmentAction
from stock_signal.fundamentals import evaluate_financials
from stock_signal.portfolio_risk import calculate_size, paired_risk
from stock_signal.research_store import financial_sync_state, load_disclosures, load_risk_settings


class ResearchInputs:
    """一回の分析要求内で共通データを共有する。要求間で古い保有を保持しない。"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._bars = {}

    @cached_property
    def settings(self):
        return load_risk_settings(self.database_url)

    @cached_property
    def holdings(self):
        return list_positions(self.database_url)

    def bars(self, symbol, provider):
        key = (symbol, provider)
        if key not in self._bars:
            self._bars[key] = load_daily_bars(self.database_url, symbol, provider=provider)
        return self._bars[key]


def enrich_analysis(
    database_url: str,
    result: AnalysisResult,
    bars: list,
    *,
    provider: str | None,
    inputs: ResearchInputs | None = None,
    technical_action: str | None = None,
) -> AnalysisResult:
    """現在の分析に制約を加える。既存の過去検証へ現在保有を混入させない。"""
    if not bars:
        return result
    as_of = bars[-1].trade_date
    decision_date = date.today()
    raw_price = bars[-1].raw_close or (bars[-1].close if not bars[-1].is_adjusted else None)
    records = (
        load_disclosures(database_url, result.symbol, decision_date + timedelta(days=1))
        if provider == "jquants"
        else []
    )
    reports = [r for r in records if "FinancialStatements" in r["DocType"]]
    report = max(
        reports,
        key=lambda r: (r.get("CurPerEn", ""), r["DiscDate"], r.get("DiscTime", ""), r["DiscNo"]),
        default={},
    )
    report_date = report.get("DiscDate", as_of.isoformat())
    split = any(
        b.adjustment_factor is not None and b.adjustment_factor != 1
        for b in bars
        if b.trade_date.isoformat() > report_date
    )
    financials = evaluate_financials(
        records, decision_date, float(raw_price) if raw_price else None, split_since_report=split
    )
    if any(r["DiscDate"] == decision_date.isoformat() for r in records):
        financials["event_status"] = "hold"
        financials["events"].append(
            "本日の財務開示があります。日次判定への反映前に内容確認が必要です"
        )
    financials["sync"] = financial_sync_state(database_url, result.symbol)
    if financials["sync"]["status"] != "success":
        if financials["status"] == "reviewed":
            financials["status"] = "partial"
        financials["cautions"].append("財務の同期が未完了・失敗または4日超未更新です")
    decision = result.investment_decision
    technical_action = technical_action or (
        decision.action.value if decision else "insufficient_data"
    )
    reasons = []
    if result.horizon_days == 20 and financials["status"] != "reviewed":
        reasons.append("中長期の企業品質・業績・評価倍率の確認が完了していません")
    if financials["event_status"] == "hold":
        reasons.extend(financials["events"])
    if (date.today() - as_of).days > 4:
        reasons.append("株価が4暦日を超えて古いため最新の分析には使えません")
    if decision and reasons:
        decision = replace(
            decision,
            action=(
                InvestmentAction.WATCH
                if decision.action == InvestmentAction.BUY_CANDIDATE
                else decision.action
            ),
            entry_stage=(
                EntryStage.NOT_APPLICABLE
                if decision.action == InvestmentAction.BUY_CANDIDATE
                else decision.entry_stage
            ),
            summary=(
                "追加確認が必要です。" + "。".join(reasons)
                if decision.action == InvestmentAction.BUY_CANDIDATE
                else decision.summary
            ),
            cautions=decision.cautions + tuple(reasons),
        )
    inputs = inputs or ResearchInputs(database_url)
    settings = inputs.settings
    holdings = inputs.holdings
    instruments = get_instruments_by_symbols(database_url, [result.symbol], provider or "jquants")
    sector = instruments[0].sector_33_code if instruments else None
    complete = bool(sector and raw_price and (date.today() - as_of).days <= 4)
    symbol_value, sector_value, total_value = Decimal(0), Decimal(0), Decimal(0)
    correlations = []
    for position in holdings:
        history = inputs.bars(position.symbol, position.provider)
        current = [b for b in history if b.trade_date <= as_of]
        last = current[-1] if current else None
        price = last.raw_close if last else None
        if price is None or not position.sector_33_code or (as_of - last.trade_date).days > 4:
            complete = False
            continue
        value = price * position.quantity
        total_value += value
        if position.symbol == result.symbol and position.provider == provider:
            symbol_value += value
        if position.sector_33_code == sector:
            sector_value += value
        if position.symbol != result.symbol or position.provider != provider:
            correlations.append({"symbol": position.symbol, **paired_risk(bars, history, as_of)})
    if settings and total_value + Decimal(settings["cash_balance"]) > Decimal(
        settings["total_capital"]
    ):
        complete = False
    # テクニカルの調整済み損切り水準を、最新の実価格の基準へ戻す。
    stop = (
        Decimal(str(decision.execution_stop_price)) * raw_price / bars[-1].close
        if decision and decision.execution_stop_price and raw_price
        else None
    )
    sizing = calculate_size(
        settings,
        price=raw_price,
        stop=stop,
        symbol_value=symbol_value,
        sector_value=sector_value,
        valuation_complete=complete,
        eligible=bool(decision and decision.action == InvestmentAction.BUY_CANDIDATE),
    )
    sizing["symbol_value"] = str(symbol_value)
    sizing["sector_value"] = str(sector_value)
    sizing["valuation_complete"] = complete
    sizing["correlations"] = correlations
    sizing["topix_risk"] = paired_risk(bars, inputs.bars("TOPIX", "jquants"), as_of)
    if any(r["correlation"] is not None and r["correlation"] >= 0.8 for r in correlations):
        sizing["cautions"].append("保有銘柄との相関が0.8以上です。分散効果は限定的です")
    if (
        settings
        and sizing["max_shares"] in (None, 0)
        and decision
        and decision.action == InvestmentAction.BUY_CANDIDATE
    ):
        decision = replace(
            decision,
            action=InvestmentAction.WATCH,
            entry_stage=EntryStage.NOT_APPLICABLE,
            summary="資金・保有リスクの制約により新規購入候補を保留しています",
            cautions=decision.cautions + tuple(sizing["cautions"]),
        )
    screening_status = "conditions_not_met"
    if technical_action == "buy_candidate":
        if decision and decision.action == InvestmentAction.BUY_CANDIDATE:
            screening_status = "candidate"
        elif result.horizon_days == 20 and financials["status"] in {"missing", "partial", "stale"}:
            screening_status = "data_pending"
        else:
            screening_status = "risk_hold"
    return replace(
        result,
        investment_decision=decision,
        research={
            "version": "light-research-v2",
            "financials": financials,
            "portfolio": sizing,
            "technical_action": technical_action,
            "screening_status": screening_status,
        },
    )
