"""双轨调度单元测试（无网络、无 LLM）。"""

from finch.engagement.flow import EngagementRunResult
from finch.graph.dual_track import run_dual_track
from finch.storage.database import RunRecord


def _original(state="COMPLETED"):
    return lambda rid: RunRecord(id=rid, state=state)


def _engagement(status="succeeded"):
    return lambda rid: EngagementRunResult(
        run_id=rid, posts_found=0, candidates=[], failures=[], status=status, summary="ok"
    )


def _raise(exc):
    def _inner(rid):
        raise exc

    return _inner


def test_both_tracks_succeed_no_partial_failure():
    result = run_dual_track(
        original_track=_original("COMPLETED"),
        engagement_track=_engagement("succeeded"),
    )
    assert result.partial_failure is False
    assert result.status == "succeeded"
    assert result.original is not None and result.original.state == "COMPLETED"
    assert result.engagement is not None and result.engagement.status == "succeeded"


def test_original_raises_engagement_preserved_and_partial_failure():
    result = run_dual_track(
        original_track=_raise(RuntimeError("boom")),
        engagement_track=_engagement("succeeded"),
    )
    assert result.partial_failure is True
    assert result.status == "partial_failure"
    assert result.original is None
    assert result.original_error is not None
    assert result.original_error.type == "RuntimeError"
    assert result.original_error.message == "boom"
    assert result.engagement is not None and result.engagement.status == "succeeded"


def test_engagement_raises_original_preserved_and_partial_failure():
    result = run_dual_track(
        original_track=_original("COMPLETED"),
        engagement_track=_raise(ValueError("nope")),
    )
    assert result.partial_failure is True
    assert result.status == "partial_failure"
    assert result.original is not None and result.original.state == "COMPLETED"
    assert result.engagement is None
    assert result.engagement_error is not None
    assert result.engagement_error.type == "ValueError"


def test_both_succeed_with_empty_engagement_not_failure():
    result = run_dual_track(
        original_track=_original("COMPLETED"),
        engagement_track=_engagement("empty"),
    )
    assert result.partial_failure is False
    assert result.status == "succeeded"
    assert result.engagement is not None and result.engagement.status == "empty"


def test_original_returned_failed_state_marks_partial_failure():
    result = run_dual_track(
        original_track=_original("FAILED"),
        engagement_track=_engagement("succeeded"),
    )
    assert result.partial_failure is True
    assert result.status == "partial_failure"


def test_engagement_returned_failed_status_marks_partial_failure():
    result = run_dual_track(
        original_track=_original("COMPLETED"),
        engagement_track=_engagement("failed"),
    )
    assert result.partial_failure is True
    assert result.status == "partial_failure"


def test_both_failed_marks_failed():
    result = run_dual_track(
        original_track=_raise(RuntimeError("x")),
        engagement_track=_engagement("failed"),
    )
    assert result.partial_failure is True
    assert result.status == "failed"


def test_shared_run_id_propagates_to_both_tracks():
    result = run_dual_track(
        run_id="run-123",
        original_track=_original("COMPLETED"),
        engagement_track=_engagement("succeeded"),
    )
    assert result.run_id == "run-123"
    assert result.original is not None and result.original.id == "run-123"
    assert result.engagement is not None and result.engagement.run_id == "run-123"
