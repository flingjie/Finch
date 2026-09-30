"""Unit tests for the inbox projection and selection service."""

from datetime import datetime
from types import SimpleNamespace

from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import Draft, DraftKind, RecommendedFormat
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.inbox.models import DecisionAction, DecisionRecord, InboxItem, InboxTrack
from finch.inbox.service import (
    build_original_item,
    list_items,
    original_score,
    select_next,
)


def _job(candidate_id=None, decision="d", tradeoff="t", why_now="w", id="job_1"):
    return ContentJob(
        id=id,
        source_card_ids=["ev_1"],
        candidate_id=candidate_id,
        reader_problem="rp",
        author_position=AuthorPosition(claim="c", decision=decision, tradeoff=tradeoff),
        recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.CONFIRMED,
        core_message="core",
        why_now=why_now,
    )


def _draft(job_id="job_1", candidate_id=None, id="draft_1"):
    return Draft(
        id=id, kind=DraftKind.ORIGINAL, candidate_id=candidate_id,
        language="zh", body="正文", content_job_id=job_id,
    )


def _card(card_id="ev_1", conf=ClaimConfidence.SUPPORTED):
    return EvidenceCard(
        id=card_id, event_id="evt", claim="claim", sources=[],
        confidence=conf, publishable=True, topics=["t"],
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


def test_select_next_orders_must_ask_then_score():
    high_must = InboxItem(
        id="b", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.1,
        must_ask=True, ask_reasons=["position_incomplete"],
    )
    original = InboxItem(
        id="a", track=InboxTrack.ORIGINAL, content_type="original",
        provenance="personal", source_refs=[], why_now="", score=0.5,
    )
    assert select_next([original, high_must]).id == "b"
    assert select_next([original]).id == "a"
    assert select_next([]) is None


def _job_no_position(candidate_id=None, why_now="w", id="job_1"):
    return _job(candidate_id=candidate_id, why_now=why_now, id=id).model_copy(
        update={"author_position": None}
    )


def _repos(*, jobs=None, drafts=None, decisions=None, cards=None):
    """轻量 stub 仓库，满足 list_items 的 duck-typed 接口。"""
    jobs_by_id = {j.id: j for j in (jobs or [])}
    return (
        SimpleNamespace(get_job=lambda jid: jobs_by_id.get(jid)),
        SimpleNamespace(list_drafts=lambda: list(drafts or [])),
        SimpleNamespace(list=lambda: list(decisions or [])),
        SimpleNamespace(list_cards=lambda: list(cards or [])),
    )


def test_list_items_returns_all_in_order():
    job_ask = _job_no_position(id="job_1")  # must_ask (position incomplete)
    job_ready = _job(decision="d", tradeoff="t", why_now="w", id="job_2")
    jobs, drafts, decisions, cards = _repos(
        jobs=[job_ask, job_ready],
        drafts=[
            _draft(job_id="job_1", id="draft_1"),
            _draft(job_id="job_2", id="draft_2"),
        ],
        cards=[_card()],
    )
    items = list_items(jobs=jobs, drafts=drafts, decisions=decisions, cards=cards)
    assert [it.id for it in items] == ["job_1", "job_2"]  # must_ask 优先
    assert all(it.track == InboxTrack.ORIGINAL for it in items)


def test_list_items_empty_returns_empty():
    jobs, drafts, decisions, cards = _repos()
    assert list_items(jobs=jobs, drafts=drafts, decisions=decisions, cards=cards) == []


def test_list_items_skips_decided_jobs():
    job_ready = _job(decision="d", tradeoff="t", why_now="w", id="job_1")
    jobs, drafts, decisions, cards = _repos(
        jobs=[job_ready],
        drafts=[_draft(job_id="job_1", id="draft_1")],
        decisions=[
            DecisionRecord(
                id="dec_job_1", job_id="job_1", draft_id="draft_1",
                action=DecisionAction.ACCEPT, approved_content_hash="h",
                decided_at=datetime.now(),
            )
        ],
        cards=[_card()],
    )
    assert list_items(jobs=jobs, drafts=drafts, decisions=decisions, cards=cards) == []
