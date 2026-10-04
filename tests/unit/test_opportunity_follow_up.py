"""Tests for connections follow-up (回应 → 接续机会)."""

from finch.opportunities.follow_up import (
    FollowUpDraft,
    follow_up_opportunity,
)
from finch.opportunities.models import (
    ContributionForm,
    EntryKind,
    Opportunity,
    Proposal,
)
from finch.opportunities.repository import OpportunityRepository
from finch.opportunities.service import OpportunityService
from finch.storage.repositories import (
    FeedbackSnapshotRepository,
    InteractionRecordRepository,
)
from finch.storage.workspace import Workspace


class FakeRunner:
    def __init__(self, draft: FollowUpDraft):
        self.draft = draft
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.draft


def _previous() -> Opportunity:
    return Opportunity(
        id="opp_prev",
        person_ref="person_1",
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与回归相关",
        why_continue="可补充边界",
        proposal=Proposal(
            contribution="方法卡",
            form=ContributionForm.METHOD_CARD,
            expected_output="一张卡",
            scope="一例",
        ),
    )


def test_follow_up_creates_next_opportunity_when_meaningful(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    service = OpportunityService(OpportunityRepository(ws))
    service.create_from(_previous())
    draft = FollowUpDraft(
        meaningful=True,
        recommend=True,
        topic="边界条件",
        contribution="补一张时间依赖对照卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="对照卡",
        why_me="对方追问了时间依赖",
        why_continue="可继续验证",
    )
    result = follow_up_opportunity(
        previous=service.get("opp_prev"),
        reply_body="我们这边时间戳没固定，重跑确实漂",
        reply_url="https://x.com/a/status/2",
        peer_id="peer_1",
        runner=FakeRunner(draft),
        service=service,
        interactions=InteractionRecordRepository(ws),
        feedbacks=FeedbackSnapshotRepository(ws),
    )
    assert result.meaningful is True
    assert result.opportunity is not None
    assert result.opportunity.previous_opportunity_id == "opp_prev"
    assert result.opportunity.proposal is not None
    assert "对照卡" in result.opportunity.proposal.expected_output
    assert result.interaction.direction == "inbound"
    assert FeedbackSnapshotRepository(ws).get(result.interaction.id) is not None


def test_follow_up_skips_when_not_meaningful(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    service = OpportunityService(OpportunityRepository(ws))
    service.create_from(_previous())
    result = follow_up_opportunity(
        previous=service.get("opp_prev"),
        reply_body="谢谢分享！",
        peer_id="peer_1",
        runner=FakeRunner(FollowUpDraft(meaningful=False, skip_reason="礼貌回应")),
        service=service,
        interactions=InteractionRecordRepository(ws),
        feedbacks=FeedbackSnapshotRepository(ws),
    )
    assert result.meaningful is False
    assert result.opportunity is None
    assert result.interaction.outcome != "meaningful"


def test_follow_up_is_idempotent_on_same_reply(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    service = OpportunityService(OpportunityRepository(ws))
    service.create_from(_previous())
    draft = FollowUpDraft(
        meaningful=True,
        recommend=True,
        contribution="下一步",
        form=ContributionForm.REPLY_DRAFT,
        expected_output="一句追问",
    )
    runner = FakeRunner(draft)
    kwargs = dict(
        previous=service.get("opp_prev"),
        reply_body="同一条回复",
        reply_url="https://x.com/a/status/9",
        peer_id="peer_1",
        runner=runner,
        service=service,
        interactions=InteractionRecordRepository(ws),
        feedbacks=FeedbackSnapshotRepository(ws),
    )
    first = follow_up_opportunity(**kwargs)
    second = follow_up_opportunity(**kwargs)
    assert second.reused_interaction is True
    assert second.interaction.id == first.interaction.id
    assert runner.calls == 1


def test_follow_up_carries_problem_fit_next_action(tmp_path):
    from finch.opportunities.models import Fit, NextAction, Problem

    ws = Workspace(tmp_path)
    ws.ensure()
    service = OpportunityService(OpportunityRepository(ws))
    service.create_from(_previous())
    draft = FollowUpDraft(
        meaningful=True,
        recommend=True,
        topic="边界条件",
        contribution="补一张时间依赖对照卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="对照卡",
        problem=Problem(statement="时间戳未固定", evidence_status="author_stated"),
        fit=Fit(reason="与回归相关", practice_refs=["agent-100-days"]),
        next_action=NextAction(type="ask", suggestion="问时间戳如何固定"),
    )
    result = follow_up_opportunity(
        previous=service.get("opp_prev"),
        reply_body="我们这边时间戳没固定，重跑确实漂",
        reply_url="https://x.com/a/status/2",
        peer_id="peer_1",
        runner=FakeRunner(draft),
        service=service,
        interactions=InteractionRecordRepository(ws),
        feedbacks=FeedbackSnapshotRepository(ws),
    )
    assert result.opportunity is not None
    assert result.opportunity.problem is not None
    assert result.opportunity.problem.evidence_status == "author_stated"
    assert result.opportunity.next_action is not None
    assert result.opportunity.next_action.type == "ask"
