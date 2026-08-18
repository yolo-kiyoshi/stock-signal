from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from stock_signal.providers.http import HttpClientError, UrllibJsonHttpClient


def test_http_error_includes_safe_api_message() -> None:
    error = HTTPError(
        "https://example.invalid",
        400,
        "Bad Request",
        {},
        BytesIO(b'{"message":"from must be within the plan period"}'),
    )

    with (
        patch("stock_signal.providers.http.urlopen", side_effect=error),
        pytest.raises(HttpClientError, match="from must be within the plan period"),
    ):
        UrllibJsonHttpClient().get_json(
            "https://example.invalid", {}, 1, headers={"x-api-key": "secret"}
        )
