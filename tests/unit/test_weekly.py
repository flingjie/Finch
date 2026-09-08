"""Unit tests for the weekly analysis (migrated to read DecisionRecord)."""

from datetime import datetime

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.learn.weekly import weekly_analysis
from finch.storage.database import Store
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    FeedbackRepository,
)


def _decision(store, job_id, draft_id, action, decided_at):
    DecisionRecordRepository(store).save(
        DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft_id, action=action,
            approved_content_hash="h", decided_at=decided_at,
        )
    )


def _seed_job_draft(store, job_id, draft_id):
    ContentJobRepository(store).upsert_job(
        ContentJob(
            id=job_id, source_card_ids=[], reader_problem="rp",
            author_position=None, recommended_format=DraftKind.ORIGINAL,
            status=ContentJobStatus.CONFIRMED,
        )
    )
    DraftRepository(store).upsert_draft(
        Draft(id=draft_id, kind=DraftKind.ORIGINAL, body="b", content_job_id=job_id)
    )


def _analyze(store):
    return weekly_analysis(
        DraftRepository(store), DecisionRecordRepository(store), FeedbackRepository(store),
        ContentJobRepository(store), CriticReportRepository(store),
    )


def test_weekly_analysis_counts_decisions(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    _seed_job_draft(store, "j1", "d1")
    _seed_job_draft(store, "j2", "d2")
    _decision(store, "j1", "d1", DecisionAction.ACCEPT, datetime(2026, 9, 1))
    _decision(store, "j2", "d2", DecisionAction.SKIP, datetime(2026, 9, 1))

    report = _analyze(store)
    assert report.approved == 1
    assert report.skipped == 1
    assert report.reviewed_drafts == 2


def test_weekly_analysis_empty(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    report = _analyze(store)
    assert report.reviewed_drafts == 0


def test_weekly_analysis_skip_reason_from_job(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    _seed_job_draft(store, "j1", "d1")
    ContentJobRepository(store).upsert_job(
        ContentJobRepository(store).get_job("j1").model_copy(
            update={"status": ContentJobStatus.SKIPPED, "reject_reason": "not_now"}
        )
    )
    _decision(store, "j1", "d1", DecisionAction.SKIP, datetime(2026, 9, 1))
    report = _analyze(store)
    assert report.skip_reasons.get("not_now") == 1
