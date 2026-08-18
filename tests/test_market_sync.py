from sqlalchemy.exc import OperationalError

from stock_signal.market_sync import _concise_error


def test_database_error_is_shortened_without_sql_statement() -> None:
    original = RuntimeError(
        "bind message has 70400 parameter formats but 0 parameters\n"
        "INSERT INTO daily_bars ... 巨大なSQL"
    )
    error = OperationalError("INSERT INTO daily_bars ...", {}, original)

    message = _concise_error(error)

    assert message == "bind message has 70400 parameter formats but 0 parameters"
    assert "INSERT INTO" not in message
