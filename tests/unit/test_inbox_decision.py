"""Unit tests for the inbox decision service (original track)."""

from finch.author.models import PublicationIntent
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind, RecommendedFormat
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


def _job(job_id="job_1"):
    return ContentJob(
        id=job_id, source_card_ids=[], reader_problem="rp",
        author_position=None, recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.CONFIRMED,
    )


def _draft(job_id="job_1"):
    return Draft(id="draft_1", kind=DraftKind.ORIGINAL, language="zh", body="正文",
                 content_job_id=job_id)


def _svc(job=None, draft=None):
    decisions = _Decisions()
    intents = _Intents()
    return InboxDecisionService(
        jobs=_Jobs(job), drafts=_Drafts(draft), decisions=decisions,
        publication_intents=intents,
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


def test_skip_original_marks_do_not_write():
    svc, decisions, _ = _svc(job=_job(), draft=_draft())
    record = svc.skip("job_1", "not_now")
    assert record.action == DecisionAction.SKIP
    assert len(decisions.saved) == 1


def test_unknown_id_raises_keyerror():
    svc, _, _ = _svc()
    try:
        svc.accept("nope")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass
