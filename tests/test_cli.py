from datetime import date

from stock_signal.cli import build_parser, main


def test_health_command(capsys) -> None:
    assert main(["health"]) == 0
    assert capsys.readouterr().out.strip() == "stock-signal: 正常"


def test_daily_dry_run(capsys) -> None:
    assert main(["daily", "--dry-run"]) == 0
    assert "差分取得 -> 検証" in capsys.readouterr().out


def test_historical_report_arguments_are_parsed() -> None:
    args = build_parser().parse_args([
        "historical-report",
        "7203",
        "--from",
        "2025-01-01",
        "--to",
        "2025-12-31",
    ])

    assert args.command == "historical-report"
    assert args.symbol == "7203"
    assert args.provider == "jquants"
    assert args.start == date(2025, 1, 1)
    assert args.end == date(2025, 12, 31)
