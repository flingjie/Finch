"""执行前守卫单元测试（Phase 5，纯函数，无网络）。"""

from datetime import datetime

from finch.engagement.guard import ExecutionStatus, evaluate_execution
from finch.engagement.models import (
    ConversationScore,
    ExternalPost,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)
from finch.settings import EngagementSettings


def _candidate(
    *,
    status: InteractionStatus = InteractionStatus.APPROVED,
    action: InteractionAction = InteractionAction.DRAFT_REPLY,
) -> InteractionCandidate:
    post = ExternalPost(
        id="p1",
        platform="x",
        url="https://x.com/alice/status/p1",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability in production?",
        published_at=datetime(2026, 9, 1, 12, 0, 0),
    )
    score = ConversationScore(
        relevance=0.8,
        novelty=0.8,
        discussability=0.8,
        practical_evidence=0.8,
        relationship_value=0.8,
        total=0.8,
        reasons=["on topic"],
    )
    return InteractionCandidate(
        id=f"x:{post.id}:{action.value}",
        post=post,
        score=score,
        action=action,
        draft="Have you tried recording a failure replay?",
        approval_required=True,
        status=status,
    )


def _ok(**overrides):
    data = dict(
        post_available=True,
        draft_unchanged=True,
        author_today_count=0,
        public_today_count=0,
        engagement=EngagementSettings(),
    )
    data.update(overrides)
    return data


def test_not_approved_returns_rejected():
    status, reasons = evaluate_execution(
        _candidate(status=InteractionStatus.PROPOSED), **_ok()
    )
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["not approved"]


def test_modified_draft_returns_rejected():
    status, reasons = evaluate_execution(_candidate(), **_ok(draft_unchanged=False))
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["draft modified since approval"]


def test_author_daily_limit_returns_rejected():
    engagement = EngagementSettings(per_author_daily_limit=1)
    status, reasons = evaluate_execution(
        _candidate(), **_ok(author_today_count=1, engagement=engagement)
    )
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["author daily limit reached"]


def test_public_reply_limit_returns_rejected():
    engagement = EngagementSettings(max_public_replies=2)
    status, reasons = evaluate_execution(
        _candidate(), **_ok(public_today_count=2, engagement=engagement)
    )
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["public reply limit reached"]


def test_post_gone_returns_rejected():
    status, reasons = evaluate_execution(_candidate(), **_ok(post_available=False))
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["post no longer available"]


def test_post_unverifiable_returns_unknown_never_approved():
    status, reasons = evaluate_execution(_candidate(), **_ok(post_available=None))
    assert status is ExecutionStatus.UNKNOWN
    assert status is not ExecutionStatus.APPROVED
    assert "could not be verified" in reasons[0]


def test_all_ok_returns_approved():
    status, reasons = evaluate_execution(_candidate(), **_ok())
    assert status is ExecutionStatus.APPROVED
    assert reasons == []


def test_first_blocker_wins_when_multiple_apply():
    # 未批准 + 草稿被修改 + 帖子不可确认：应只报第一个阻断项（未批准），而非 UNKNOWN。
    status, reasons = evaluate_execution(
        _candidate(status=InteractionStatus.PROPOSED),
        **_ok(draft_unchanged=False, post_available=None),
    )
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["not approved"]
