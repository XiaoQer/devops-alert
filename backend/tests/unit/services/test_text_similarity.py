from incident_intelligence.domain.alert_text_similarity import normalize_alert_text
from incident_intelligence.services.text_similarity import (
    SemanticSimilarityResult,
    TextSimilarityService,
)

LEFT = normalize_alert_text("支付错误率升高", "HTTP 5xx 超过阈值", "HighErrorRate", "errors")
RIGHT = normalize_alert_text("支付接口 5xx 增加", "错误率持续升高", "HighErrorRate", "errors")


class FailingProvider:
    def similarity(self, left, right):
        raise TimeoutError("不得泄露的提供方异常")


class WorkingProvider:
    def similarity(self, left, right):
        return SemanticSimilarityResult(
            score=0.9,
            model_name="safe-embedding",
            model_version="v1",
        )


class InvalidProvider:
    def similarity(self, left, right):
        return {"score": 2.0, "model_name": "bad", "model_version": "v1"}


def test_missing_provider_uses_deterministic_result() -> None:
    result = TextSimilarityService().compare(LEFT, RIGHT)

    assert result.provider_status == "DISABLED"
    assert result.score == result.deterministic_score
    assert result.semantic_score is None


def test_provider_failure_falls_back_without_error_detail() -> None:
    result = TextSimilarityService(provider=FailingProvider()).compare(LEFT, RIGHT)

    assert result.provider_status == "FALLBACK"
    assert result.score == result.deterministic_score
    assert "异常" not in result.model_dump_json()


def test_invalid_provider_score_falls_back() -> None:
    result = TextSimilarityService(provider=InvalidProvider()).compare(LEFT, RIGHT)

    assert result.provider_status == "FALLBACK"
    assert result.semantic_score is None


def test_valid_provider_contributes_bounded_semantic_score() -> None:
    result = TextSimilarityService(provider=WorkingProvider()).compare(LEFT, RIGHT)

    assert result.provider_status == "USED"
    assert result.semantic_score == 0.9
    assert result.model_name == "safe-embedding"
    assert result.model_version == "v1"
    assert result.deterministic_score <= result.score <= 0.9
