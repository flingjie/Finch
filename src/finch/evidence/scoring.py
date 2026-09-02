"""Runtime scoring formula and quality gates (Phase 4 Task B2)."""

from datetime import datetime

from finch.evidence.models import JudgeScores, MatchResult
from finch.settings import QualityGates


def formula_score(scores: JudgeScores, timing: float, relationship_value: float) -> float:
    """Weighted score for a matched candidate.

    Discussability is intentionally excluded from the formula.
    """
    return (
        0.30 * scores.relevance
        + 0.30 * scores.evidence_strength
        + 0.20 * scores.incremental_value
        + 0.10 * timing
        + 0.10 * relationship_value
    )


def timing_value(published_at: datetime | None, default: float) -> float:
    """Timing score; no decay — only missing timestamps fall back to default."""
    if published_at is None:
        return default
    return 1.0


def relationship_value(
    author_handle: str,
    *,
    high_value_authors: list[str],
    blocked_authors: list[str],
) -> float:
    """Relationship score based on author lists (case-insensitive, no @ prefix)."""
    handle = author_handle.lstrip("@").lower()
    high = {h.lstrip("@").lower() for h in high_value_authors}
    blocked = {b.lstrip("@").lower() for b in blocked_authors}

    if handle in blocked:
        return 0.0
    if handle in high:
        return 1.0
    return 0.5


def apply_gates(results: list[MatchResult], gates: QualityGates) -> list[MatchResult]:
    """Filter, sort, and truncate match results by quality gates."""
    kept = [
        r
        for r in results
        if r.score >= gates.min_candidate_score
        and r.scores.evidence_strength >= gates.min_evidence_score
        and r.scores.discussability >= gates.min_discussability
    ]
    kept.sort(key=lambda r: r.score, reverse=True)
    return kept[: gates.max_daily_replies]
