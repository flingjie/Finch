"""互动质量指标聚合单元测试（Phase 7，纯函数，无 IO）。"""

from datetime import UTC, datetime

import pytest

from finch.engagement.metrics import (
    EngagementMetrics,
    compute_metrics,
    render_metrics,
    summarize_run_stats,
)
from finch.engagement.models import (
    ConversationEvidence,
    ConversationScore,
    EngagementRunStats,
    ExternalPost,
    FeedbackSnapshot,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)


def _candidate(
    pid: str = "p1",
    *,
    action: InteractionAction = InteractionAction.DRAFT_REPLY,
    status: InteractionStatus = InteractionStatus.PROPOSED,
    draft: str | None = "draft body",
    revised_draft: str | None = None,
    approval_required: bool = True,
) -> InteractionCandidate:
    post = ExternalPost(
        id=pid,
        platform="x",
        url=f"https://x.com/alice/status/{pid}",
        author_id="alice",
        author_name="Alice",
        content="How do you test agent reliability in production systems?",
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
        id=f"x:{pid}:{action.value}",
        post=post,
        score=score,
        action=action,
        draft=draft,
        revised_draft=revised_draft,
        approval_required=approval_required,
        status=status,
    )


def _snapshot(iid: str = "x:p1:draft_reply", *, meaningful: bool = True) -> FeedbackSnapshot:
    return FeedbackSnapshot(
        id=f"snap-{iid}",
        interaction_id=iid,
        meaningful=meaningful,
        captured_at=datetime(2026, 9, 2, tzinfo=UTC),
    )


def _evidence(iid: str = "x:p1:draft_reply", *, verified: bool = False) -> ConversationEvidence:
    return ConversationEvidence(
        id=f"ev-{iid}",
        interaction_id=iid,
        post_id="p1",
        kind="hypothesis",
        statement="maybe failure replay helps",
        verified=verified,
    )


def test_empty_inputs_return_all_zeroes():
    m = compute_metrics([], [], [])
    assert m.interactions_executed == 0
    assert m.draft_approval_rate == 0.0
    assert m.user_edit_distance == 0.0
    assert m.meaningful_response_rate == 0.0
    assert m.conversation_to_personal_evidence_rate == 0.0
    assert m.duplicate_or_low_value_rate == 0.0


def test_interactions_executed_counts_only_executed():
    interactions = [
        _candidate(pid="p1", status=InteractionStatus.EXECUTED),
        _candidate(pid="p2", status=InteractionStatus.APPROVED),
        _candidate(pid="p3", status=InteractionStatus.PROPOSED),
    ]
    assert compute_metrics(interactions, [], []).interactions_executed == 1


def test_draft_approval_rate_over_approval_required_only():
    interactions = [
        _candidate(pid="p1", status=InteractionStatus.APPROVED),
        _candidate(pid="p2", status=InteractionStatus.PROPOSED),
        # bookmark 不需要批准，不计入草稿批准率分母。
        _candidate(
            pid="p3",
            action=InteractionAction.BOOKMARK,
            draft=None,
            approval_required=False,
            status=InteractionStatus.APPROVED,
        ),
    ]
    assert compute_metrics(interactions, [], []).draft_approval_rate == pytest.approx(0.5)


def test_draft_approval_rate_zero_when_no_approval_required():
    interactions = [
        _candidate(
            pid="p1",
            action=InteractionAction.BOOKMARK,
            draft=None,
            approval_required=False,
        )
    ]
    assert compute_metrics(interactions, [], []).draft_approval_rate == 0.0


def test_user_edit_distance_known_edit():
    # "abcd" -> "abce"：SequenceMatcher ratio = 2*3/8 = 0.75，距离 = 0.25。
    interactions = [
        _candidate(pid="p1", draft="abcd", revised_draft="abce"),
    ]
    assert compute_metrics(interactions, [], []).user_edit_distance == pytest.approx(0.25)


def test_user_edit_distance_average_over_edited_only():
    # p1 编辑距离 0.25；p2 只有草稿无修订（不参与）；p3 修订与草稿相同（参与，距离 0）。
    interactions = [
        _candidate(pid="p1", draft="abcd", revised_draft="abce"),
        _candidate(pid="p2", draft="abcd", revised_draft=None),
        _candidate(pid="p3", draft="abcd", revised_draft="abcd"),
    ]
    # 有 revised_draft 的候选才参与平均：(0.25 + 0.0) / 2 = 0.125。
    assert compute_metrics(interactions, [], []).user_edit_distance == pytest.approx(0.125)


def test_user_edit_distance_zero_when_no_revisions():
    interactions = [_candidate(pid="p1", draft="abcd", revised_draft=None)]
    assert compute_metrics(interactions, [], []).user_edit_distance == 0.0


def test_meaningful_response_rate():
    snapshots = [
        _snapshot("i1", meaningful=True),
        _snapshot("i2", meaningful=True),
        _snapshot("i3", meaningful=False),
    ]
    assert compute_metrics([], snapshots, []).meaningful_response_rate == pytest.approx(2 / 3)


def test_meaningful_response_rate_zero_when_no_snapshots():
    assert compute_metrics([], [], []).meaningful_response_rate == 0.0


def test_conversation_to_personal_evidence_rate():
    evidence = [
        _evidence("i1", verified=True),
        _evidence("i2", verified=False),
        _evidence("i3", verified=False),
    ]
    assert (
        compute_metrics([], [], evidence).conversation_to_personal_evidence_rate
        == pytest.approx(1 / 3)
    )


def test_duplicate_or_low_value_rate():
    interactions = [
        _candidate(pid="p1", status=InteractionStatus.REJECTED),
        _candidate(pid="p2", action=InteractionAction.IGNORE, status=InteractionStatus.PROPOSED),
        _candidate(pid="p3", status=InteractionStatus.APPROVED),
    ]
    assert compute_metrics(interactions, [], []).duplicate_or_low_value_rate == pytest.approx(
        2 / 3
    )


def test_render_metrics_includes_primary_labels():
    out = render_metrics(EngagementMetrics(draft_approval_rate=0.5, user_edit_distance=0.25))
    assert "草稿批准率: 50%" in out
    assert "用户编辑距离: 0.250" in out
    assert "实质回复率" in out
    assert "conversation→personal 证据率" in out
    assert "重复/低价值率" in out
    assert "已执行互动" in out


def test_summarize_run_stats_counts_no_evidence_runs():
    stats = [
        EngagementRunStats(run_id="r1", posts_scanned=0, candidates=0, drafts=0, latency_ms=1),
        EngagementRunStats(run_id="r2", posts_scanned=5, candidates=1, drafts=1, latency_ms=2),
        EngagementRunStats(run_id="r3", posts_scanned=0, candidates=0, drafts=0, latency_ms=3),
    ]
    summary = summarize_run_stats(stats)
    assert summary.total_posts_scanned == 5
    assert summary.total_runs == 3
    assert summary.no_evidence_runs == 2


def test_summarize_run_stats_empty():
    summary = summarize_run_stats([])
    assert summary.total_posts_scanned == 0
    assert summary.total_runs == 0
    assert summary.no_evidence_runs == 0
