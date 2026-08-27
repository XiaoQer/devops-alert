from __future__ import annotations

import math
import re
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field

_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_TIMESTAMP = re.compile(r"\b\d{4}-\d{2}-\d{2}[tT][0-9:.+-]+(?:[zZ])?\b")
_POD_SUFFIX = re.compile(r"(?<=[a-z0-9])-[a-z0-9]{6,10}-[a-z0-9]{1,5}\b")
_LONG_HEX = re.compile(r"\b[0-9a-f]{12,}\b", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"\b\d{6,}\b")
_PUNCTUATION = re.compile(r"[^a-z0-9\u4e00-\u9fff<>]+")
_TOKENS = re.compile(r"<[a-z]+>|[a-z0-9]+|[\u4e00-\u9fff]")


class NormalizedAlertText(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    normalized: str = Field(max_length=4096)
    tokens: tuple[str, ...] = Field(max_length=256)
    character_ngrams: tuple[str, ...] = Field(max_length=512)


def normalize_alert_text(
    title: str,
    summary: str,
    alertname: str,
    symptom: str,
) -> NormalizedAlertText:
    bounded_parts = (title[:512], summary[:1024], alertname[:256], symptom[:128])
    value = " ".join(bounded_parts).casefold()
    for pattern, placeholder in (
        (_UUID, " <uuid> "),
        (_IPV4, " <ip> "),
        (_TIMESTAMP, " <time> "),
        (_POD_SUFFIX, "-<resource>"),
        (_LONG_HEX, " <id> "),
        (_LONG_NUMBER, " <number> "),
    ):
        value = pattern.sub(placeholder, value)
    normalized = " ".join(_PUNCTUATION.sub(" ", value).split())[:4096]
    tokens = tuple(_TOKENS.findall(normalized)[:256])
    compact = "".join(tokens)
    character_ngrams = tuple(compact[index : index + 3] for index in range(len(compact) - 2))[:512]
    return NormalizedAlertText(
        normalized=normalized,
        tokens=tokens,
        character_ngrams=character_ngrams,
    )


def deterministic_similarity(left: NormalizedAlertText, right: NormalizedAlertText) -> float:
    token_score = _weighted_jaccard(left.tokens, right.tokens)
    gram_score = _cosine_sparse(left.character_ngrams, right.character_ngrams)
    return round(min(1.0, max(0.0, 0.6 * token_score + 0.4 * gram_score)), 6)


def _weighted_jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_counts = Counter(left)
    right_counts = Counter(right)
    keys = left_counts.keys() | right_counts.keys()
    if not keys:
        return 1.0
    intersection = sum(min(left_counts[key], right_counts[key]) for key in keys)
    union = sum(max(left_counts[key], right_counts[key]) for key in keys)
    return intersection / union


def _cosine_sparse(left: tuple[str, ...], right: tuple[str, ...]) -> float:
    left_counts = Counter(left)
    right_counts = Counter(right)
    if not left_counts and not right_counts:
        return 1.0
    if not left_counts or not right_counts:
        return 0.0
    numerator = sum(value * right_counts[key] for key, value in left_counts.items())
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    return numerator / (left_norm * right_norm)
