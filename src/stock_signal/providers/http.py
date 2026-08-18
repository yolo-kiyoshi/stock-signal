from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class HttpClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class JsonHttpClient(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, str],
        timeout: float,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any: ...


class BinaryHttpClient(Protocol):
    def get_bytes(self, url: str, timeout: float) -> bytes: ...


class UrllibJsonHttpClient:
    """Python標準ライブラリだけを使う差し替え可能なJSONクライアント。"""

    user_agent = "stock-signal/0.1"

    def get_json(
        self,
        url: str,
        params: Mapping[str, str],
        timeout: float,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        request_url = f"{url}?{urlencode(params)}"
        request = Request(
            request_url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
                **(dict(headers) if headers else {}),
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return json.load(response)
        except HTTPError as error:
            detail = ""
            try:
                body = error.read().decode("utf-8", errors="replace")
                payload = json.loads(body)
                if isinstance(payload, Mapping):
                    detail = str(payload.get("message") or payload.get("detail") or "")
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                detail = ""
            message = f"市場データAPIがHTTP {error.code}を返しました"
            if detail:
                message = f"{message}: {detail[:300]}"
            raise HttpClientError(
                message,
                status_code=error.code,
            ) from error
        except (URLError, TimeoutError, OSError) as error:
            raise HttpClientError("市場データAPIへ接続できませんでした") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise HttpClientError("市場データAPIが不正なJSONを返しました") from error


class UrllibBinaryHttpClient:
    """署名付きダウンロードURLからバイト列を取得する。"""

    user_agent = "stock-signal/0.1"

    def get_bytes(self, url: str, timeout: float) -> bytes:
        request = Request(url, headers={"User-Agent": self.user_agent})
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            raise HttpClientError("市場データファイルを取得できませんでした") from error
