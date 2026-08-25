from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from stock_signal.ai_review import (
    InvestmentReviewError,
    OpenAIInvestmentReviewService,
    review_text_segments,
)


class FakeResponses:
    def __init__(self, output) -> None:
        self.output = output
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_test",
            model="gpt-test",
            status="completed",
            incomplete_details=None,
            output=self.output,
            output_text="",
        )


def _client(output):
    return SimpleNamespace(responses=FakeResponses(output))


def test_ai_review_uses_web_search_and_preserves_clickable_citation() -> None:
    text = "【AI最終確認区分】保留\n最新情報を確認しました。[出典]"
    start = text.index("[出典]")
    output = [
        SimpleNamespace(type="web_search_call"),
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(
                    type="output_text",
                    text=text,
                    annotations=[
                        SimpleNamespace(
                            type="url_citation",
                            start_index=start,
                            end_index=start + len("[出典]"),
                            url="https://example.com/disclosure",
                            title="企業の公式発表",
                        )
                    ],
                )
            ],
        ),
    ]
    client = _client(output)
    service = OpenAIInvestmentReviewService(
        "test-key",
        "gpt-test",
        client=client,
        now=lambda: datetime(2026, 8, 25, 9, 0, tzinfo=UTC),
    )

    review = service.review(
        symbol="7203",
        display_name="トヨタ自動車",
        horizon_days=5,
        provider="jquants",
        technical_context={
            "as_of_date": "2026-08-24",
            "direction": "up",
            "score_is_probability": False,
        },
    )

    assert review.search_performed is True
    assert review.report_text == text
    assert review.citations[0].url == "https://example.com/disclosure"
    assert review.technical_as_of_date == "2026-08-24"
    call = client.responses.calls[0]
    assert call["tools"] == [{"type": "web_search"}]
    assert call["store"] is False
    assert call["max_tool_calls"] == 6
    assert call["max_output_tokens"] == 6_000
    assert call["include"] == ["web_search_call.action.sources"]
    assert '"score_is_probability": false' in call["input"]
    assert "上昇確率などへ変換しない" in call["instructions"]

    segments = review_text_segments(review.report_text, review.citations)
    linked = next(segment for segment in segments if "citation" in segment)
    assert linked["text"] == "[出典]"
    assert linked["citation"]["title"] == "企業の公式発表"


def test_ai_review_rejects_result_without_web_search() -> None:
    output = [
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(
                    type="output_text",
                    text="検索を行っていない回答",
                    annotations=[],
                )
            ],
        )
    ]
    service = OpenAIInvestmentReviewService(
        "test-key",
        "gpt-test",
        client=_client(output),
    )

    with pytest.raises(InvestmentReviewError, match="Web検索"):
        service.review(
            symbol="7203",
            display_name="トヨタ自動車",
            horizon_days=5,
            provider="jquants",
            technical_context={"as_of_date": "2026-08-24"},
        )


def test_ai_review_discards_non_web_citation_urls() -> None:
    text = "不正な参照[出典]"
    output = [
        SimpleNamespace(type="web_search_call"),
        SimpleNamespace(
            type="message",
            content=[
                SimpleNamespace(
                    type="output_text",
                    text=text,
                    annotations=[
                        SimpleNamespace(
                            type="url_citation",
                            start_index=5,
                            end_index=len(text),
                            url="javascript:alert(1)",
                            title="不正URL",
                        )
                    ],
                )
            ],
        ),
    ]
    service = OpenAIInvestmentReviewService(
        "test-key",
        "gpt-test",
        client=_client(output),
    )

    review = service.review(
        symbol="7203",
        display_name="トヨタ自動車",
        horizon_days=5,
        provider="jquants",
        technical_context={"as_of_date": "2026-08-24"},
    )

    assert review.citations == ()


def test_ai_review_does_not_display_incomplete_output() -> None:
    class IncompleteResponses:
        def create(self, **_kwargs):
            return SimpleNamespace(
                id="resp_incomplete",
                model="gpt-test",
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
                output=[
                    SimpleNamespace(
                        type="message",
                        content=[
                            SimpleNamespace(
                                type="output_text",
                                text="途中までの回答",
                                annotations=[],
                            )
                        ],
                    )
                ],
            )

    service = OpenAIInvestmentReviewService(
        "test-key",
        "gpt-test",
        client=SimpleNamespace(responses=IncompleteResponses()),
    )

    with pytest.raises(InvestmentReviewError, match="出力上限"):
        service.review(
            symbol="7203",
            display_name="トヨタ自動車",
            horizon_days=20,
            provider="jquants",
            technical_context={"as_of_date": "2026-08-24"},
        )
