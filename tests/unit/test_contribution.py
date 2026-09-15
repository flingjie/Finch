"""Deterministic contribution assessment unit tests."""

from datetime import UTC, datetime

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import RecommendedFormat
from finch.engagement.contribution import assess_contribution, lived_claim_allowed
from finch.engagement.models import ExternalPost

_NOW = datetime(2026, 9, 15, tzinfo=UTC)


def _post(content: str, topics: list[str] | None = None) -> ExternalPost:
    return ExternalPost(
        id="p1",
        platform="x",
        url="https://x.com/u/1",
        author_id="u",
        author_name="U",
        content=content,
        published_at=_NOW,
        matched_topics=topics or [],
    )


def test_empty_material_no_contribution():
    ok, reason = assess_contribution(_post("how do you test agents?"))
    assert ok is False
    assert "暂不回复" in reason


def test_lived_claim_allowed_only_when_observed_with_facts():
    job = ContentJob(
        id="j1",
        source_card_ids=[],
        reader_problem="r",
        author_position=None,
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.PROPOSED,
        evidence_status="observed",
        facts=["ran the harness once"],
    )
    assert lived_claim_allowed(job) is True
    external = job.model_copy(update={"evidence_status": "externally_reported"})
    assert lived_claim_allowed(external) is False
    empty = job.model_copy(update={"facts": [], "observation": ""})
    assert lived_claim_allowed(empty) is False
