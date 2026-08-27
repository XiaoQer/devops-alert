from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from incident_intelligence.domain.alert_text_similarity import (
    NormalizedAlertText,
    deterministic_similarity,
)


class SemanticSimilarityResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    model_name: str = Field(min_length=1, max_length=128)
    model_version: str = Field(min_length=1, max_length=128)


class TextSimilarityProvider(Protocol):
    def similarity(
        self,
        left: NormalizedAlertText,
        right: NormalizedAlertText,
    ) -> SemanticSimilarityResult: ...


class CombinedTextSimilarity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    deterministic_score: float = Field(ge=0.0, le=1.0)
    semantic_score: float | None = Field(default=None, ge=0.0, le=1.0)
    provider_status: Literal["DISABLED", "USED", "FALLBACK"]
    model_name: str | None = Field(default=None, max_length=128)
    model_version: str | None = Field(default=None, max_length=128)


class TextSimilarityService:
    def __init__(self, provider: TextSimilarityProvider | None = None) -> None:
        self._provider = provider

    def compare(
        self,
        left: NormalizedAlertText,
        right: NormalizedAlertText,
    ) -> CombinedTextSimilarity:
        deterministic_score = deterministic_similarity(left, right)
        if self._provider is None:
            return _fallback(deterministic_score, "DISABLED")
        try:
            semantic = SemanticSimilarityResult.model_validate(
                self._provider.similarity(left, right)
            )
        # 外部能力必须安全退化且不泄露异常详情。
        except Exception:
            return _fallback(deterministic_score, "FALLBACK")
        return CombinedTextSimilarity(
            score=round(0.7 * deterministic_score + 0.3 * semantic.score, 6),
            deterministic_score=deterministic_score,
            semantic_score=semantic.score,
            provider_status="USED",
            model_name=semantic.model_name,
            model_version=semantic.model_version,
        )


def _fallback(
    deterministic_score: float,
    status: Literal["DISABLED", "FALLBACK"],
) -> CombinedTextSimilarity:
    return CombinedTextSimilarity(
        score=deterministic_score,
        deterministic_score=deterministic_score,
        semantic_score=None,
        provider_status=status,
        model_name=None,
        model_version=None,
    )
