"""ResponsivenessChecker 单元测试：回应型草稿是否留下回应空间。"""

from finch.content.checkers.base import CheckContext
from finch.content.checkers.responsiveness import ResponsivenessChecker
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind, RecommendedFormat


def _job(fmt: RecommendedFormat) -> ContentJob:
    return ContentJob(
        id="job_1", source_card_ids=[], reader_problem="r",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        recommended_format=fmt, status=ContentJobStatus.CONFIRMED,
    )


def _draft(body: str) -> Draft:
    return Draft(id="d", kind=DraftKind.REPLY, body=body, content_job_id="job_1")


def _ctx(body: str, fmt: RecommendedFormat) -> CheckContext:
    return CheckContext(draft=_draft(body), cards=[], job=_job(fmt))


def test_reply_with_question_passes():
    result = ResponsivenessChecker().check(
        _ctx("Have you tried recording a failure replay?", RecommendedFormat.REPLY)
    )
    assert result.passed is True


def test_reply_with_invitation_passes():
    result = ResponsivenessChecker().check(
        _ctx("Replays helped here — what do you think?", RecommendedFormat.QUOTE)
    )
    assert result.passed is True


def test_reply_without_room_for_response_fails():
    result = ResponsivenessChecker().check(
        _ctx("Replays are the right approach here.", RecommendedFormat.REPLY)
    )
    assert result.passed is False
    assert result.severity == "medium"
    assert "response" in result.issues[0]


def test_original_content_is_not_checked():
    result = ResponsivenessChecker().check(
        _ctx("Replays are the right approach here.", RecommendedFormat.SHORT_POST)
    )
    assert result.passed is True


def test_empty_reply_passes():
    result = ResponsivenessChecker().check(_ctx("", RecommendedFormat.REPLY))
    assert result.passed is True
