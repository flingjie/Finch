"""Unit tests for the inbox decision service (original + engagement dispatch)."""

from datetime import datetime

from finch.author.models import PublicationIntent
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.inbox.service import InboxDecisionService


class _Jobs:
    def __init__(self, job):
        self._job = job

    def get_job(self, jid):
        return self._job if self._job and self._job.id == jid else None

    def upsert_job(self, job):
        self._job = job


class _Drafts:
    def __init__(self, draft):
        self._draft = draft

    def list_by_job(self, jid):
        return [self._draft] if self._draft and self._draft.content_job_id == jid else []


class _Decisions:
    def __init__(self):
        self.saved = []

    def save(self, r):
        self.saved.append(r)

    def list(self):
        return self.saved


class _Intents:
    def __init__(self):
        self.saved = []

    def save(self, i):
        self.saved.append(i)


class _Interactions:
    def __init__(self, cand):
        self._cand = cand

    def get(self, cid):
        return self._cand if self._cand and self._cand.id == cid else None

    def approve(self, cid):
        self._cand = self._cand.model_copy(update={"status": InteractionStatus.APPROVED})

    def reject(self, cid, reason):
        self._cand = self._cand.model_copy(
            update={"status": InteractionStatus.REJECTED, "reject_reason": reason}
        )


def _job(job_id="job_1"):
    return ContentJob(
        id=job_id, source_card_ids=[], reader_problem="rp",
        author_position=None, recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.CONFIRMED,
    )


def _draft(job_id="job_1"):
    return Draft(id="draft_1", kind=DraftKind.ORIGINAL, language="zh", body="正文",
                 content_job_id=job_id)


def _candidate(cand_id="x:p1:reply"):
    return InteractionCandidate(
        id=cand_id,
        post=ExternalPost(id="p1", platform="x", url="u", author_id="a", author_name="A",
                          content="x" * 30, published_at=datetime.now()),
        score=ConversationScore(relevance=0.5, novelty=0.5, discussability=0.5,
                                practical_evidence=0.5, relationship_value=0.5,
                                total=0.5, reasons=[]),
        action=InteractionAction.DRAFT_REPLY, approval_required=True,
    )


def _svc(job=None, draft=None, cand=None):
    decisions = _Decisions()
    intents = _Intents()
    return InboxDecisionService(
        jobs=_Jobs(job), drafts=_Drafts(draft), decisions=decisions,
        publication_intents=intents, interactions=_Interactions(cand),
    ), decisions, intents


def test_accept_original_writes_decision_and_intent_only():
    svc, decisions, intents = _svc(job=_job(), draft=_draft())
    record = svc.accept("job_1")
    assert isinstance(record, DecisionRecord)
    assert record.action == DecisionAction.ACCEPT
    assert record.approved_content_hash
    assert len(decisions.saved) == 1
    assert len(intents.saved) == 1
    assert isinstance(intents.saved[0], PublicationIntent)


def test_accept_engagement_approves_candidate():
    cand = _candidate()
    svc, decisions, _ = _svc(cand=cand)
    out = svc.accept("x:p1:reply")
    assert out.status.value == "approved"
    assert len(decisions.saved) == 0  # 互动不写 DecisionRecord


def test_skip_original_marks_do_not_write():
    svc, decisions, _ = _svc(job=_job(), draft=_draft())
    record = svc.skip("job_1", "not_now")
    assert record.action == DecisionAction.SKIP
    assert len(decisions.saved) == 1


def test_skip_engagement_rejects_candidate():
    cand = _candidate()
    svc, _, _ = _svc(cand=cand)
    out = svc.skip("x:p1:reply", "not_now")
    assert out.status.value == "rejected"
    assert out.reject_reason == "not_now"


def test_unknown_id_raises_keyerror():
    svc, _, _ = _svc()
    try:
        svc.accept("nope")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
