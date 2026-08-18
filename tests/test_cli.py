from stock_signal.cli import main


def test_health_command(capsys) -> None:
    assert main(["health"]) == 0
    assert capsys.readouterr().out.strip() == "stock-signal: 正常"


def test_daily_dry_run(capsys) -> None:
    assert main(["daily", "--dry-run"]) == 0
    assert "差分取得 -> 検証" in capsys.readouterr().out
