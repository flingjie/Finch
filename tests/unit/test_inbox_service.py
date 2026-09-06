"""Unit tests for the inbox projection and selection service."""

from datetime import datetime

from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    ContentScope,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import Draft, DraftKind
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
)
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.inbox.models import InboxItem, InboxTrack
from finch.inbox.service import (
    build_engagement_item,
    build_original_item,
    original_score,
    select_next,
)


def _job(candidate_id=None, decision="d", tradeoff="t", why_now="w"):
    return ContentJob(
        id="job_1",
        source_card_ids=["ev_1"],
        candidate_id=candidate_id,
        reader_problem="rp",
        audience="aud",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(claim="c", decision=decision, tradeoff=tradeoff),
        success_criteria=[SuccessCriterion(id="s", description="d", measurement="human")],
        recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.READY,
        core_message="core",
        why_now=why_now,
        scope=ContentScope.BOUNDED_LESSON,
    )


def _draft(job_id="job_1", candidate_id=None):
    return Draft(
        id="draft_1", kind=DraftKind.ORIGINAL, candidate_id=candidate_id,
        language="zh", body="正文", content_job_id=job_id,
    )


def _card(card_id="ev_1", conf=ClaimConfidence.SUPPORTED):
    return EvidenceCard(
        id=card_id, event_id="evt", claim="claim", sources=[],
        confidence=conf, publishable=True, topics=["t"],
    )


def _candidate(cand_id="x:p1:reply", action=InteractionAction.DRAFT_REPLY):
    return InteractionCandidate(
        id=cand_id,
        post=ExternalPost(
            id="p1", platform="x", url="https://x.com/u/1", author_id="a",
            author_name="A", content="a post long enough", published_at=datetime.now(),
        ),
        score=ConversationScore(
            relevance=0.5, novelty=0.5, discussability=0.5,
            practical_evidence=0.5, relationship_value=0.5, total=0.5, reasons=[],
        ),
        action=action,
        draft="回复正文",
        approval_required=True,
    )


def test_original_score_is_deterministic():
    a = original_score(_job(), {"ev_1": _card()})
    b = original_score(_job(), {"ev_1": _card()})
    assert a == b
    assert 0.0 <= a <= 1.0


def test_build_original_item_projection():
    item = build_original_item(
        _job(), _draft(), cards_by_id={"ev_1": _card()},
        must_ask=False, ask_reasons=[], risks=[],
    )
    assert item.id == "job_1"
    assert item.track == InboxTrack.ORIGINAL
    assert item.content_type == "original"
    assert item.provenance == "personal"
    assert item.draft == "正文"
    assert item.draft_id == "draft_1"
    assert item.position == {"claim": "c", "decision": "d", "tradeoff": "t"}


def test_build_original_item_idea_provenance():
    job = _job()
    job = job.model_copy(update={"id": "idea_abc123"})
    item = build_original_item(
        job, _draft("idea_abc123"), cards_by_id={},
        must_ask=False, ask_reasons=[], risks=[],
    )
    assert item.provenance == "idea"


def test_build_engagement_item_projection():
    item = build_engagement_item(_candidate())
    assert item.id == "x:p1:reply"
    assert item.track == InboxTrack.ENGAGEMENT
    assert item.content_type == "reply"
    assert item.provenance == "external"
    assert item.draft == "回复正文"
    assert item.score == 0.5
    assert item.source_refs == ["https://x.com/u/1"]


def test_select_next_orders_must_ask_then_original_then_score():
    high_must = InboxItem(
        id="b", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.1,
        must_ask=True, ask_reasons=["position_incomplete"],
    )
    original = InboxItem(
        id="a", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.5,
    )
    engagement = InboxItem(
        id="c", track=InboxTrack.ENGAGEMENT, content_type="reply",
        provenance="external", source_refs=[], why_now="", score=0.99,
    )
    assert select_next([engagement, original, high_must]).id == "b"
    assert select_next([engagement, original]).id == "a"
    assert select_next([]) is None
