"""Unit tests for the weekly analysis (migrated to read DecisionRecord)."""

from datetime import datetime

from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind, RecommendedFormat
from finch.inbox.models import DecisionAction, DecisionRecord
from finch.learn.weekly import weekly_analysis
from finch.storage.repositories import (
    ContentJobRepository,
    CriticReportRepository,
    DecisionRecordRepository,
    DraftRepository,
    FeedbackRepository,
)
from finch.storage.workspace import Workspace


def _decision(ws, job_id, draft_id, action, decided_at):
    DecisionRecordRepository(ws).save(
        DecisionRecord(
            id=f"dec_{job_id}", job_id=job_id, draft_id=draft_id, action=action,
            approved_content_hash="h", decided_at=decided_at,
        )
    )


def _seed_job_draft(ws, job_id, draft_id):
    ContentJobRepository(ws).upsert_job(
        ContentJob(
            id=job_id, source_card_ids=[], reader_problem="rp",
            author_position=None, recommended_format=RecommendedFormat.SHORT_POST,
            status=ContentJobStatus.CONFIRMED,
        )
    )
    DraftRepository(ws).upsert_draft(
        Draft(id=draft_id, kind=DraftKind.ORIGINAL, body="b", content_job_id=job_id)
    )


def _analyze(ws):
    return weekly_analysis(
        DraftRepository(ws), DecisionRecordRepository(ws), FeedbackRepository(ws),
        ContentJobRepository(ws), CriticReportRepository(ws),
    )


def test_weekly_analysis_counts_decisions(tmp_path):
    ws = Workspace(tmp_path)
    _seed_job_draft(ws, "j1", "d1")
    _seed_job_draft(ws, "j2", "d2")
    _decision(ws, "j1", "d1", DecisionAction.ACCEPT, datetime(2026, 9, 1))
    _decision(ws, "j2", "d2", DecisionAction.SKIP, datetime(2026, 9, 1))

    report = _analyze(ws)
    assert report.approved == 1
    assert report.skipped == 1
    assert report.reviewed_drafts == 2


def test_weekly_analysis_empty(tmp_path):
    ws = Workspace(tmp_path)
    report = _analyze(ws)
    assert report.reviewed_drafts == 0


def test_weekly_analysis_skip_reason_from_job(tmp_path):
    ws = Workspace(tmp_path)
    _seed_job_draft(ws, "j1", "d1")
    ContentJobRepository(ws).upsert_job(
        ContentJobRepository(ws).get_job("j1").model_copy(
            update={"status": ContentJobStatus.SKIPPED, "reject_reason": "not_now"}
        )
    )
    _decision(ws, "j1", "d1", DecisionAction.SKIP, datetime(2026, 9, 1))
    report = _analyze(ws)
    assert report.skip_reasons.get("not_now") == 1
