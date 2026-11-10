"""BPSの基準日・確認期限・変更検出と、確認履歴の保存を検証する。"""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from test_light_research import bars_for_risk, report

from stock_signal.bps import bps_limits
from stock_signal.fundamentals import evaluate_financials, normalize_summary
from stock_signal.research_store import load_bps_review, save_bps_review
from stock_signal.web.app import app

DAY = date(2026, 8, 3)


def records(**changes):
    return [
        report(2025),
        report(DiscNo="fy", DiscDate="2026-05-10", CurPerEn="2026-03-31",
               CurPerType="FY", DocType="FYFinancialStatements_Consolidated_IFRS"),
        report(BPS="", **changes),
    ]


def evaluate(rows=None, **kwargs):
    return evaluate_financials(rows or records(), DAY, 1000, **kwargs)


def confirmation(result):
    ref = result["bps_reference"]
    return {"fingerprint": ref["fingerprint"], "checked_on": DAY.isoformat(),
            "source_disclosure_no": ref["source_disclosure_no"],
            "evidence_url": "https://example.com/report", "notes": "基準日以降の資本変動を確認済み"}


def test_historical_bps_requires_review_and_preserves_source():
    result = evaluate()
    ref = result["bps_reference"]
    assert ref["source_period_end"] == "2026-03-31"
    assert ref["source_disclosed_date"] == "2026-05-10"
    assert ref["source_disclosure_no"] == "fy"
    assert ref["source_value"] == 500
    assert ref["value"] is None
    assert result["metrics"]["pbr"] is None
    assert ref["reviewable"]
    checked = evaluate(bps_review=confirmation(result))
    assert checked["metrics"]["pbr"] == 2
    assert checked["status"] == "reviewed"
    assert checked["bps_reference"]["status"] == "historical_reviewed"
    assert checked["bps_reference"]["valid_until"] == "2026-09-02"


@pytest.mark.parametrize("change", [
    {"NP": "-1"}, {"SigChgInC": "true"}, {"RetroRst": "true"},
    {"ChgByASRev": "true"}, {"ChgNoASRev": "true"}, {"ChgAcEst": "true"},
    {"ShOutFY": "110"}, {"TrShFY": "20"}, {"Cap": "110"},
])
def test_detected_changes_cannot_be_overridden_by_review(change):
    rows = records()
    rows[1].update(ShOutFY="100", TrShFY="10", Cap="100")
    baseline = evaluate(rows)
    rows[-1].update(change)
    result = evaluate(rows, bps_review=confirmation(baseline))
    assert result["metrics"]["pbr"] is None
    assert result["bps_reference"]["status"] == "blocked"
    assert not result["bps_reference"]["reviewable"]


def test_split_between_period_end_and_disclosure_blocks_past_bps():
    result = evaluate(capital_events=["2026-04-15:0.5"])
    assert result["bps_reference"]["status"] == "blocked"
    assert result["metrics"]["pbr"] is None


def test_source_period_loss_is_already_reflected_in_source_bps():
    rows = records()
    rows[1]["NP"] = "-1"
    assert evaluate(rows)["bps_reference"]["reviewable"]
    checked = evaluate(rows, bps_review=confirmation(evaluate(rows)))
    assert checked["metrics"]["pbr"] == 2


def test_same_period_requires_identical_balance_and_price_coverage():
    old = report(DiscNo="old", DiscDate="2026-07-30", ShEq="1000", ShOutFY="100", TrShFY="0")
    latest = report(BPS="", ShEq="1000", ShOutFY="100", TrShFY="0")
    args = dict(capital_events=[], coverage_start=date(2026, 6, 1))
    result = evaluate([report(2025), old, latest], **args)
    assert result["bps_reference"]["status"] == "same_period"
    assert result["metrics"]["pbr"] == 2
    latest.pop("ShEq")
    assert evaluate([old, latest], **args)["metrics"]["pbr"] is None


@pytest.mark.parametrize("checked_on", ["2026-07-03", "2026-08-04"])
def test_expired_or_future_review_does_not_supply_a_value(checked_on):
    review = confirmation(evaluate())
    review["checked_on"] = checked_on
    assert evaluate(bps_review=review)["metrics"]["pbr"] is None


def test_reference_age_uses_balance_sheet_date_not_recent_disclosure():
    result = evaluate(bps_max_age_days=100)
    assert result["bps_reference"]["status"] == "expired"
    assert result["metrics"]["pbr"] is None


def test_new_disclosure_invalidates_manual_review_even_with_same_bps_source():
    review = confirmation(evaluate())
    rows = records() + [report(DiscNo="revision", DiscDate="2026-08-02",
                               DocType="EarnForecastRevision_Consolidated_IFRS")]
    result = evaluate(rows, bps_review=review)
    assert result["metrics"]["pbr"] is None
    assert result["bps_reference"]["review_status"] == "invalidated"


def test_manual_bps_confirmation_does_not_override_high_per():
    rows = records(FEPS="10")
    result = evaluate(rows, bps_review=confirmation(evaluate(rows)))
    assert result["metrics"]["pbr"] == 2
    assert result["status"] == "caution"


def test_nonpositive_current_bps_and_incompatible_basis_never_use_older_value():
    rows = records()
    rows[-1]["BPS"] = "-5"
    assert evaluate(rows)["metrics"]["pbr"] is None
    rows[-1].update(BPS="", DocType="1QFinancialStatements_NonConsolidated_JP")
    assert evaluate(rows)["bps_reference"]["source_disclosure_no"] is None


def test_confirmation_cancellation_is_append_only(database_url):
    from sqlalchemy import func, select

    from stock_signal.persistence.engine import get_engine
    from stock_signal.persistence.schema import bps_reviews

    save_bps_review(database_url, "7203", confirmation(evaluate()))
    save_bps_review(database_url, "7203", {"status": "revoked", "checked_on": DAY.isoformat()})
    assert load_bps_review(database_url, "7203")["status"] == "revoked"
    with get_engine(database_url).connect() as connection:
        assert connection.scalar(select(func.count()).select_from(bps_reviews)) == 2


def test_review_api_requires_current_context_and_explicit_checks(database_url, monkeypatch):
    financials = evaluate()
    financials["sync"] = {"status": "success"}
    monkeypatch.setattr("stock_signal.web.bps_review.assess_financials", lambda *a: financials)
    bar = replace(bars_for_risk()[-1], trade_date=date.today())
    monkeypatch.setattr("stock_signal.web.bps_review.load_daily_bars", lambda *a, **kw: [bar])
    payload = {k: v for k, v in confirmation(financials).items() if k != "checked_on"}
    client = TestClient(app)
    url = "/api/v1/financials/7203/bps-review"
    assert client.post(url, json=payload).status_code == 422
    payload.update(capital_checked=True, basis_checked=True, equity_checked=True)
    assert client.post(url, json={**payload, "fingerprint": "0" * 64}).status_code == 409
    assert client.post(url, json=payload).status_code == 200
    saved = load_bps_review(database_url, "7203")
    assert saved["source_value"] == 500
    assert saved["checked_on"] == date.today().isoformat()
    assert client.delete(url).status_code == 200
    assert load_bps_review(database_url, "7203")["status"] == "revoked"
    financials["sync"]["status"] = "stale"
    assert client.post(url, json=payload).status_code == 409


def test_review_recheck_keeps_other_financial_conditions(database_url, monkeypatch):
    from stock_signal.domain.analysis import (
        AnalysisResult,
        Direction,
        InvestmentAction,
        InvestmentDecision,
    )
    from stock_signal.research import enrich_analysis

    rows = records(FEPS="10")
    financials = evaluate(rows, bps_review=confirmation(evaluate(rows)))
    monkeypatch.setattr("stock_signal.research.assess_financials", lambda *a, **kw: financials)
    result = AnalysisResult(
        "7203", date.today().isoformat(), 20, Direction.UP, {Direction.UP: 80}, (), "test", "test",
        investment_decision=InvestmentDecision(InvestmentAction.BUY_CANDIDATE, 80, "候補", (), ()),
    )
    bar = replace(bars_for_risk()[-1], trade_date=date.today())
    result = enrich_analysis(database_url, result, [bar], provider="jquants")
    assert result.investment_decision.action == InvestmentAction.WATCH
    assert result.research["screening_status"] == "financial_caution"


def test_config_limits_and_treasury_shares(monkeypatch):
    assert normalize_summary(report(TrShFY="100"))["TrShFY"] == "100"
    monkeypatch.setenv("BPS_MAX_AGE_DAYS", "365")
    monkeypatch.setenv("BPS_REVIEW_MAX_AGE_DAYS", "7")
    assert bps_limits() == (365, 7)
    monkeypatch.setenv("BPS_MAX_AGE_DAYS", "0")
    with pytest.raises(ValueError):
        bps_limits()


def test_review_boundary_is_inclusive():
    review = confirmation(evaluate())
    review["checked_on"] = (DAY - timedelta(days=30)).isoformat()
    assert evaluate(bps_review=review)["metrics"]["pbr"] == 2


@pytest.mark.parametrize("change", ["review", "disclosure", "expiry"])
def test_saved_candidates_are_invalidated_before_pagination(database_url, change):
    from sqlalchemy import update
    from test_candidate_consistency import seed_snapshots

    from stock_signal.database import list_market_candidates
    from stock_signal.persistence.engine import get_engine
    from stock_signal.persistence.schema import analysis_snapshots
    from stock_signal.research_store import save_disclosures

    seed_snapshots(database_url, 20)
    if change == "review":
        save_bps_review(database_url, "1003", confirmation(evaluate()))
    elif change == "disclosure":
        save_disclosures(database_url, [report(Code="10030")])
    else:
        with get_engine(database_url).begin() as conn:
            conn.execute(update(analysis_snapshots).where(
                analysis_snapshots.c.symbol == "1003",
            ).values(analysis_json=analysis_snapshots.c.analysis_json.op("||")({
                "research": {"financials": {"bps_reference": {
                    "valid_until": (date.today() - timedelta(days=1)).isoformat(),
                }}},
            })))
    candidates = list_market_candidates(database_url, horizon_days=20, action="buy_candidate")
    assert [r["symbol"] for r in candidates] == ["1004"]
