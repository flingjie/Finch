"""Unit tests for the idea service."""

from datetime import UTC, datetime, timedelta

from finch.author.models import AuthorPost
from finch.content.checkers.aggregate import AggregateOutcome
from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJobStatus, ContentScope
from finch.content.models import DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.idea.models import AssessIdeaOutput, IdeaAssessment, RewriteIdeaOutput, WriteIdeaOutput
from finch.idea.service import (
    assess_idea,
    build_content_job,
    build_draft,
    critic_failure_reason,
    idea_checker_suite,
    recent_author_posts,
    rewrite_idea,
    run_idea_critic,
    write_idea,
)


def _assessment(**overrides) -> AssessIdeaOutput:
    data = dict(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        reader_problem="很多工程师只把 Graph 理解成流程可视化",
        audience="正在构建生产 Agent 的工程师",
        understand="Graph 的核心价值是恢复与重放",
        believe="用可恢复性评价 Graph",
        action="重新审视 Graph 的选型",
        claim="Graph 的主要价值是支持恢复与重放",
        decision="用可恢复性而不是图的复杂度评价 Graph",
        tradeoff="需要持久化状态并处理副作用幂等",
        change_mind_if=None,
    )
    data.update(overrides)
    return AssessIdeaOutput(**data)


def test_build_content_job_sets_author_position():
    job = build_content_job("我觉得 Agent Graph 的价值是恢复", _assessment())
    assert job.id.startswith("idea_")
    assert job.source_card_ids == ["ev_1"]
    assert job.candidate_id is None
    assert job.recommended_format == DraftKind.ORIGINAL
    assert job.status == ContentJobStatus.CONFIRMED
    assert job.scope == ContentScope.BOUNDED_LESSON
    assert job.author_position is not None
    assert job.author_position.decision == "用可恢复性而不是图的复杂度评价 Graph"
    assert job.intended_effect.understand == "Graph 的核心价值是恢复与重放"
    assert job.success_criteria[0].id == "idea_human_review"
    assert job.success_criteria[0].measurement == "human"


def test_build_content_job_deterministic_id():
    a = build_content_job("同一个想法", _assessment())
    b = build_content_job("同一个想法", _assessment())
    assert a.id == b.id


def test_build_draft_idea_fields():
    job = build_content_job("想法", _assessment())
    draft = build_draft(job, "样稿正文")
    assert draft.id == f"draft_{job.id}"
    assert draft.kind == DraftKind.ORIGINAL
    assert draft.language == "zh"
    assert draft.claims == []
    assert draft.content_job_id == job.id
    assert draft.run_id == "idea"
    assert draft.position_statement == "用可恢复性而不是图的复杂度评价 Graph"


def _post(remote_id, published_at, kind="original"):
    return AuthorPost(
        platform="x",
        remote_post_id=remote_id,
        author_account_id="acct",
        kind=kind,
        body=f"post {remote_id}",
        url=f"https://x.com/u/{remote_id}",
        published_at=published_at,
    )


def test_recent_author_posts_sorts_filters_limits():
    now = datetime.now(UTC)
    posts = [
        _post("a", now - timedelta(days=10)),
        _post("b", now - timedelta(days=1)),
        _post("c", now - timedelta(days=3), kind="quote"),
        _post("d", now - timedelta(days=2), kind="reply"),
    ]
    out = recent_author_posts(posts, limit=3)
    ids = [p.remote_post_id for p in out]
    assert ids == ["b", "d", "a"]
    assert "c" not in ids


def test_idea_assessment_round_trips():
    result = IdeaAssessment(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        draft_id="draft_idea_abc",
        sample="很多 Agent 项目引入 Graph 只是为了画出来……",
    )
    back = IdeaAssessment.model_validate(result.model_dump(mode="json"))
    assert back == result
    assert back.status == "ready"
    assert back.reason_code is None


def test_assess_output_inherits_idea_assessment_fields():
    out = AssessIdeaOutput(
        status="not_ready",
        reason_code="TOO_BROAD",
        reason="没有具体问题",
        reader_problem=None,
        audience=None,
    )
    assert isinstance(out, IdeaAssessment)
    assert out.reason_code == "TOO_BROAD"
    assert out.sample is None
    assert out.matched_evidence_ids == []


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _card(card_id="ev_1"):
    return EvidenceCard(
        id=card_id,
        event_id="evt",
        claim="replay 让失败可重放",
        sources=[],
        confidence=ClaimConfidence.SUPPORTED,
        publishable=True,
        topics=["graph"],
    )


def test_assess_idea_calls_runner_once():
    ret = AssessIdeaOutput(status="ready", core_point="p", matched_evidence_ids=["ev_1"])
    runner = FakeRunner(ret)
    out = assess_idea(runner, "想法", [_card()], [])
    assert runner.calls == 1
    assert out == ret


def test_write_idea_returns_body():
    runner = FakeRunner(WriteIdeaOutput(body="样稿正文"))
    body = write_idea(runner, "想法", _assessment(), [_card()])
    assert runner.calls == 1
    assert body == "样稿正文"


def test_rewrite_idea_keeps_draft_identity_updates_body():
    job = build_content_job("想法", _assessment())
    draft = build_draft(job, "旧正文")
    runner = FakeRunner(RewriteIdeaOutput(body="新正文"))
    out = rewrite_idea(
        runner,
        draft,
        [CheckResult(checker="specificity", passed=False, severity="medium",
                     issues=["vague"], rewrite_instructions=["be specific"])],
        job,
    )
    assert runner.calls == 1
    assert out.body == "新正文"
    assert out.id == draft.id
    assert out.claims == []
    assert out.content_job_id == job.id


def _pass_check():
    return CheckResult(checker="scripted", passed=True, severity="low")


def _rewrite_check():
    return CheckResult(
        checker="scripted", passed=False, severity="medium",
        issues=["vague"], rewrite_instructions=["be specific"],
    )


def _reject_check():
    return CheckResult(
        checker="safety", passed=False, severity="hard_fail", issues=["unsafe"],
    )


class ScriptedChecker:
    name = "scripted"

    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    def check(self, ctx):
        result = self._results[self._i]
        self._i = min(self._i + 1, len(self._results) - 1)
        return result


def _critic_draft_and_job(body="正文"):
    job = build_content_job("想法", _assessment())
    return build_draft(job, body), job


def test_idea_checker_suite_drops_evidence():
    suite = idea_checker_suite(runner=None)
    names = [c.name for c in suite]
    assert "evidence" not in names
    assert len(suite) == 7


def test_run_idea_critic_passes():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_pass_check()])]
    outcome, checks, final = run_idea_critic(
        None, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.PASS
    assert final == draft


def test_run_idea_critic_rewrites_then_passes():
    draft, job = _critic_draft_and_job("旧正文")
    suite = [ScriptedChecker([_rewrite_check(), _pass_check()])]
    runner = FakeRunner(RewriteIdeaOutput(body="新正文"))
    outcome, checks, final = run_idea_critic(
        runner, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.PASS
    assert final.body == "新正文"
    assert runner.calls == 1


def test_run_idea_critic_rejects_on_hard_fail():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_reject_check()])]
    outcome, checks, final = run_idea_critic(
        None, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.REJECT
    assert final == draft


def test_run_idea_critic_rewrite_exhausted():
    draft, job = _critic_draft_and_job()
    suite = [ScriptedChecker([_rewrite_check()])]
    runner = FakeRunner(RewriteIdeaOutput(body="还是不够"))
    outcome, checks, final = run_idea_critic(
        runner, draft, job, [], max_rewrite_rounds=1, checkers=suite
    )
    assert outcome == AggregateOutcome.REWRITE


def test_critic_failure_reason_maps_safety():
    code, reason = critic_failure_reason([_reject_check()])
    assert code == "UNSAFE_TO_PUBLISH"
    assert "safety" in reason


def test_critic_failure_reason_defaults_to_no_new_value():
    code, reason = critic_failure_reason([_rewrite_check()])
    assert code == "NO_NEW_VALUE"
    assert "scripted" in reason
