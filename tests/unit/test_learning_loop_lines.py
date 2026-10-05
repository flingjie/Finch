"""learning_loop_lines：把三个验收信号渲染成行。"""

from datetime import UTC, datetime, timedelta

from finch.content.jobs import ContentJob, ContentJobStatus, PositionRevision
from finch.content.models import RecommendedFormat
from finch.learn.reflection import learning_loop_lines
from finch.practice.attempts import PracticeAttempt

NOW = datetime.now(UTC)


def _attempt(status, updated_at):
    return PracticeAttempt(
        id=f"attempt_{status}", problem="P", attempt="T", observation="O",
        status=status, created_at=NOW, updated_at=updated_at,
    )


def test_counts_verified_attempts():
    lines = learning_loop_lines(
        attempts=[_attempt("verified", NOW), _attempt("open", NOW)],
        jobs=[],
        interactions=[],
        since=NOW - timedelta(days=7),
    )
    assert any("verified attempts" in line and "1" in line for line in lines)


def test_counts_judgment_changes_and_repeats():
    job = ContentJob(
        id="idea_1", source_card_ids=[], reader_problem="r",
        recommended_format=RecommendedFormat.SHORT_POST, status=ContentJobStatus.PROPOSED,
        position_revisions=[PositionRevision(
            claim="重试值得", change_reason="同行反馈：输入稳定时才值得",
            created_at=NOW,
        )],
    )

    class _Rec:
        peer_id = "p1"

    lines = learning_loop_lines(
        attempts=[], jobs=[job], interactions=[_Rec(), _Rec()],
        since=NOW - timedelta(days=7),
    )
    assert any("judgment changes" in line and "1" in line for line in lines)
    assert any("repeat interactions" in line and "1" in line for line in lines)
