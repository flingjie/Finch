"""Writer/Critic/Daily Brief 节点 7–9 测试（Phase 5 Task F6）。"""

import json
import re

from finch.codex.runner import CodexRunner
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    PlanTopicsOutput,
    SuccessCriterion,
    TopicProposal,
)
from finch.content.models import ClaimRef, Draft, DraftKind, DraftWarning
from finch.evidence.models import ClaimConfidence, EvidenceCard, JudgeScores, MatchResult
from finch.graph.content_nodes import (
    default_checker_suite,
    make_brief_node,
    make_critique_node,
    make_define_jobs_node,
    make_draft_node,
    make_position_gate_node,
)
from finch.graph.context import items_payload
from finch.graph.events import NodeResult
from finch.graph.nodes import Node
from finch.graph.runtime import GraphRuntime
from finch.settings import QualityGates
from finch.storage.database import NodeRecord, Store
from finch.storage.repositories import ContentJobRepository
from finch.twitter.models import DiscussionCandidate


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


class Seed(Node):
    model_config = {"extra": "allow"}

    def run(self, ctx):
        return NodeResult(status="succeeded", output=self.seed)


def _brief_seeds(
    *,
    drafts: dict | None = None,
    jobs: dict | None = None,
    cards: dict | None = None,
    candidates: dict | None = None,
    matches: dict | None = None,
    gate: dict | None = None,
) -> list[Node]:
    """brief 节点上游全部 reads 的 Seed（Task 3.1 新增 ready_jobs/candidates/match_results）。"""
    return [
        Seed(name="draft", writes="drafts", seed=drafts or items_payload([])),
        Seed(name="match_evidence", writes="match_results", seed=matches or items_payload([])),
        Seed(name="define_jobs", writes="content_jobs", seed=jobs or items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=cards or items_payload([])),
        Seed(name="collect_tweets", writes="candidates", seed=candidates or items_payload([])),
        Seed(name="position_gate", writes="ready_jobs", seed=gate or items_payload([])),
    ]


def _run_brief(tmp_path, nodes):
    """跑 GraphRuntime 到 brief，返回 (run, brief payload dict)。"""
    store = _store(tmp_path)
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    return run, json.loads(rec.output_json)


def _brief_body(payload) -> str:
    return payload["items"][0]["body"]


def test_brief_node_terminal_state(tmp_path):
    # 无稿 → COMPLETED
    nodes = [*_brief_seeds(), make_brief_node(QualityGates())]
    run = GraphRuntime(_store(tmp_path), nodes).run()
    assert run.state == "COMPLETED"


def test_brief_node_waiting_when_drafts(tmp_path):
    from finch.content.models import Draft, DraftKind

    d = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    nodes = [*_brief_seeds(drafts=items_payload([d])), make_brief_node(QualityGates())]
    run = GraphRuntime(_store(tmp_path), nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"


def _card():
    return EvidenceCard(
        id="ev1",
        event_id="e",
        claim="token bucket rate limiting",
        sources=[],
        confidence=ClaimConfidence.VERIFIED,
        publishable=True,
        topics=["rate"],
    )


def _match():
    return MatchResult(
        candidate_id="t1",
        card_ids=["ev1"],
        scores=JudgeScores(
            relevance=0.9, evidence_strength=0.9, incremental_value=0.9, discussability=0.9
        ),
        timing=1.0,
        relationship_value=0.5,
        score=0.9,
    )


def _candidate():
    return DiscussionCandidate(
        id="t1",
        author_handle="u",
        text="token bucket for the agent loop",
        url="https://x.com/u/status/1",
    )


def _reply_draft():
    return Draft(
        id="d1",
        kind=DraftKind.REPLY,
        candidate_id="t1",
        language="en",
        body="hi",
        claims=[
            ClaimRef(statement="x", evidence_card_id="ev1", confidence=ClaimConfidence.VERIFIED)
        ],
    )


def _position(decision="use token bucket", tradeoff="more memory", confirmed=True):
    return AuthorPosition(
        claim="token bucket is the right call",
        decision=decision,
        tradeoff=tradeoff,
        confirmed=confirmed,
    )


_DEFAULT_POSITION = _position()


def _job(
    job_id="job1",
    candidate_id="t1",
    source_card_ids=("ev1",),
    position=_DEFAULT_POSITION,
    status=ContentJobStatus.READY,
):
    return ContentJob(
        id=job_id,
        source_card_ids=list(source_card_ids),
        candidate_id=candidate_id,
        reader_problem="readers don't know how to rate limit",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="token bucket rate limiting"),
        author_position=position,
        success_criteria=[
            SuccessCriterion(id="c1", description="critic passes", measurement="critic")
        ],
        recommended_format=DraftKind.REPLY,
        status=status,
    )


class FakeJobsRunner(CodexRunner):
    def __init__(self, jobs):
        self.jobs = jobs
        self.calls = 0

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        if output_model is PlanTopicsOutput:
            # Phase 1（plan）：每个 job 对应一个主题，主题 id 由 fake 控制为 tp0..tpN。
            return PlanTopicsOutput(
                items=[
                    TopicProposal(
                        id=f"tp{i}",
                        title="",
                        card_ids=list(j.source_card_ids),
                        candidate_id=j.candidate_id,
                    )
                    for i, j in enumerate(self.jobs)
                ]
            )
        if output_model is ContentJob:
            # Phase 2（expand）：expand_content_job 把主题 JSON 嵌进 prompt，解析出 tpN。
            match = re.search(r'"id"\s*:\s*"(tp\d+)"', prompt)
            return self.jobs[int(match.group(1)[2:])]
        raise AssertionError(f"unexpected output_model {output_model}")


def test_draft_node_writes_reply_and_original(tmp_path):
    original = Draft(
        id="d2",
        kind=DraftKind.ORIGINAL,
        candidate_id=None,
        language="zh",
        body="日记",
        claims=[
            ClaimRef(statement="x", evidence_card_id="ev1", confidence=ClaimConfidence.VERIFIED)
        ],
    )

    reply_job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    original_job = _job(job_id="job2", candidate_id=None, source_card_ids=("ev1",))

    def write_reply(runner, match, candidate, cards_by_id, job):
        assert job is not None
        return _reply_draft()

    def write_original(runner, cards, job):
        assert job is not None
        return original

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload([reply_job, original_job])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    assert "d1" in rec.output_json and "d2" in rec.output_json


def test_draft_node_empty_ready_jobs_writes_empty(tmp_path):
    calls = {"reply": 0, "original": 0}

    def write_reply(runner, match, candidate, cards_by_id, job):
        calls["reply"] += 1
        return _reply_draft()

    def write_original(runner, cards, job):
        calls["original"] += 1
        return _reply_draft()

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") == '{"items":[]}'
    assert calls == {"reply": 0, "original": 0}


def _failed_check(
    checker: str = "specificity",
    severity: str = "high",
    requires_human_input: bool = False,
    locations: tuple = ("sentence[0]",),
    issue: str = "vague",
    instruction: str = "be specific",
) -> CheckResult:
    return CheckResult(
        checker=checker,
        passed=False,
        severity=severity,  # type: ignore[arg-type]
        locations=list(locations),
        issues=[issue],
        rewrite_instructions=[instruction],
        requires_human_input=requires_human_input,
    )


def _pass_check(checker: str = "specificity") -> CheckResult:
    return CheckResult(checker=checker, passed=True, severity="low")


class SeqChecker:
    """Fake checker that returns a scripted sequence of CheckResults, then repeats the last."""

    name = "seq"

    def __init__(self, results: list[CheckResult]):
        self._results = list(results)
        self.calls = 0

    def check(self, ctx) -> CheckResult:
        self.calls += 1
        idx = min(self.calls - 1, len(self._results) - 1)
        return self._results[idx]


def _critique_nodes(rewrite, checker, gates=None):
    return [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="position_gate", writes="ready_jobs", seed=items_payload([])),
        make_critique_node(
            CodexRunner(), rewrite, gates or QualityGates(), checkers=[checker]
        ),
    ]


def test_critique_node_rewrites_until_pass(tmp_path):
    calls = {"rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "v2"})
    checker = SeqChecker([_failed_check(), _pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _critique_nodes(rewrite, checker)).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "v2" in rec.output_json
    assert calls == {"rewrite": 1}
    assert checker.calls == 2


def test_critique_node_drops_unfixable_draft(tmp_path):
    checker = SeqChecker([_failed_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft.model_copy(update={"body": "v2"})

    store = _store(tmp_path)
    run = GraphRuntime(
        store,
        _critique_nodes(rewrite, checker, gates=QualityGates(max_rewrite_rounds=2)),
    ).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert json.loads(rec.output_json)["items"] == []
    assert any("failed critique" in w for w in json.loads(rec.output_json)["warnings"])


def test_critique_node_keeps_draft_fixed_by_single_rewrite(tmp_path):
    calls = {"rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})
    checker = SeqChecker([_failed_check(), _pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(
        store,
        _critique_nodes(rewrite, checker, gates=QualityGates(max_rewrite_rounds=1)),
    ).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"rewrite": 1}


def test_critique_node_keeps_draft_fixed_by_second_rewrite(tmp_path):
    calls = {"rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})
    checker = SeqChecker([_failed_check(), _failed_check(), _pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        if calls["rewrite"] < 2:
            return draft
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(
        store,
        _critique_nodes(rewrite, checker, gates=QualityGates(max_rewrite_rounds=2)),
    ).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"rewrite": 2}
    assert checker.calls == 3


def test_critique_node_warns_on_invalid_rewritten_claims():
    invalid = _reply_draft().model_copy(
        update={
            "claims": [
                ClaimRef(
                    statement="x", evidence_card_id="ev_999", confidence=ClaimConfidence.VERIFIED
                )
            ]
        }
    )
    checker = SeqChecker([_failed_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return invalid

    node = make_critique_node(CodexRunner(), rewrite, QualityGates(), checkers=[checker])
    result = node.run(
        {
            "drafts": items_payload([_reply_draft()]),
            "match_results": items_payload([_match()]),
            "evidence_cards": items_payload([_card()]),
        }
    )
    assert result.status == "succeeded"
    assert result.output["items"] == []
    assert any("invalid claims" in w for w in result.warnings)
    # F2: warnings are also embedded in the persisted output (runtime only persists output).
    assert any("invalid claims" in w for w in result.output["warnings"])


def test_critique_node_drops_hard_fail_draft(tmp_path):
    checker = SeqChecker(
        [
            _failed_check(
                checker="evidence",
                severity="hard_fail",
                locations=("claim[0]",),
                issue="unsupported claim",
                instruction="re-bind the claim",
            )
        ]
    )
    calls = {"rewrite": 0}

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        return draft

    store = _store(tmp_path)
    run = GraphRuntime(store, _critique_nodes(rewrite, checker)).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    assert payload["items"] == []
    assert any("rejected" in w and "evidence" in w for w in payload["warnings"])
    # hard_fail is dropped immediately, never rewritten
    assert calls == {"rewrite": 0}


def test_critique_node_emits_draft_warnings(tmp_path):
    """Task 3.4：critique 输出结构化 draft_warnings（draft_id/checker/message 绑定）。"""
    checker = SeqChecker(
        [
            _failed_check(
                checker="evidence",
                severity="hard_fail",
                locations=("claim[0]",),
                issue="unsupported claim",
                instruction="re-bind the claim",
            )
        ]
    )

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft

    store = _store(tmp_path)
    run = GraphRuntime(store, _critique_nodes(rewrite, checker)).run()
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    draft_warnings = [DraftWarning.model_validate(w) for w in payload["draft_warnings"]]
    assert [w.draft_id for w in draft_warnings] == ["d1"]
    assert draft_warnings[0].checker == "evidence"
    assert "rejected by evidence" in draft_warnings[0].message


def test_critique_node_stops_on_needs_input():
    checker = SeqChecker(
        [
            _failed_check(
                checker="decision",
                severity="high",
                requires_human_input=True,
                issue="missing decision",
                instruction="state the decision",
            )
        ]
    )

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft

    node = make_critique_node(CodexRunner(), rewrite, QualityGates(), checkers=[checker])
    result = node.run(
        {
            "drafts": items_payload([_reply_draft()]),
            "match_results": items_payload([_match()]),
            "evidence_cards": items_payload([_card()]),
        }
    )
    assert result.status == "needs_input"
    assert any("decision" in w for w in result.warnings)


def test_critique_node_passes_only_failed_checks_to_rewrite(tmp_path):
    captured: list[list[CheckResult]] = []
    checker = SeqChecker([_failed_check(), _pass_check()])
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        captured.append(failed_checks)
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _critique_nodes(rewrite, checker)).run()
    assert run.state == "CRITIQUED"
    assert len(captured) == 1
    assert [c.checker for c in captured[0]] == ["specificity"]
    assert all(not c.passed for c in captured[0])


def test_critique_node_emits_per_round_reports(tmp_path):
    checker = SeqChecker([_failed_check(), _pass_check()])
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _critique_nodes(rewrite, checker)).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    reports = json.loads(rec.output_json)["reports"]
    assert len(reports) == 2
    assert reports[0] == {
        "draft_id": "d1",
        "round": 0,
        "version": _reply_draft().model_dump(mode="json"),
        "checks": [
            _failed_check().model_dump(mode="json"),
        ],
        "outcome": "rewrite",
    }
    assert reports[1]["round"] == 1
    assert reports[1]["outcome"] == "pass"
    assert reports[1]["version"]["body"] == "fixed"


def test_critique_node_runs_checkers_in_parallel(tmp_path):
    import threading

    # 串行实现会在第一个 checker 上阻塞至 barrier 超时（BrokenBarrierError）；并行后
    # 两个 checker 同时到达 barrier，立即放行。
    barrier = threading.Barrier(2, timeout=5)

    class BarrierChecker:
        def __init__(self, name):
            self.name = name

        def check(self, ctx):
            barrier.wait()
            return _pass_check(self.name)

    checkers = [BarrierChecker("c1"), BarrierChecker("c2")]

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="position_gate", writes="ready_jobs", seed=items_payload([])),
        make_critique_node(CodexRunner(), rewrite, QualityGates(), checkers=checkers),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"


def test_define_jobs_node_produces_and_filters_jobs(tmp_path):
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    bad = _job(job_id="j2", candidate_id=None, source_card_ids=("ev1", "ev_999"))
    runner = FakeJobsRunner([good, bad])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    assert "j1" in rec.output_json
    assert "j2" not in rec.output_json
    assert runner.calls == 2  # 1 plan + 1 expand（bad 主题在预过滤阶段即被剔除）


def test_position_gate_ready_when_confirmed():
    node = make_position_gate_node()
    result = node.run(
        {"content_jobs": items_payload([_job(job_id="j1", position=_position(confirmed=True))])}
    )
    assert result.status == "succeeded"
    assert [j["id"] for j in result.output["items"]] == ["j1"]


def test_position_gate_skips_do_not_write():
    node = make_position_gate_node()
    job = _job(job_id="j1", status=ContentJobStatus.DO_NOT_WRITE, position=None)
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "succeeded"
    assert result.output["items"] == []


def test_position_gate_unconfirmed_needs_input():
    node = make_position_gate_node()
    job = _job(job_id="j1", position=_position(confirmed=False))
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"
    assert [j["id"] for j in result.output["items"]] == ["j1"]


def test_position_gate_missing_position_needs_input():
    node = make_position_gate_node()
    job = _job(job_id="j1", position=None)
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"
    assert [j["id"] for j in result.output["items"]] == ["j1"]


def test_position_gate_empty_decision_needs_input():
    node = make_position_gate_node()
    job = _job(job_id="j1", position=_position(decision="", confirmed=True))
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"


def test_position_gate_only_primary_blocks():
    """Task 2.4：只有 primary job 可阻塞；其余 job 保存为 deferred，不触发 needs_input。"""
    node = make_position_gate_node()
    ready = _job(job_id="j1", candidate_id="t1", position=_position(confirmed=True))
    unconfirmed = _job(job_id="j2", candidate_id=None, position=_position(confirmed=False))
    do_not_write = _job(
        job_id="j3", status=ContentJobStatus.DO_NOT_WRITE, position=None
    )
    result = node.run(
        {"content_jobs": items_payload([ready, unconfirmed, do_not_write])}
    )
    # 只有 primary（j1，已确认）进入 ready_jobs。
    assert result.status == "succeeded"
    assert [j["id"] for j in result.output["items"]] == ["j1"]
    # j2 被 deferred（not now），j3（DO_NOT_WRITE）不参与选择、也不出现在 deferred。
    assert [d["job_id"] for d in result.output["deferred"]] == ["j2"]


def test_position_gate_resume_proceeds_once_confirmed(tmp_path):
    store = _store(tmp_path)
    unconfirmed = _job(job_id="j1", candidate_id=None, position=_position(confirmed=False))
    confirmed = _job(job_id="j1", candidate_id=None, position=_position(confirmed=True))

    def nodes():
        return [
            Seed(name="define_jobs", writes="content_jobs", seed=items_payload([unconfirmed])),
            make_position_gate_node(),
            Seed(name="draft", writes="drafts", seed=items_payload([]), succeeds_to="DRAFTED"),
        ]

    run1 = GraphRuntime(store, nodes()).run()
    assert run1.state == "NEEDS_INPUT"
    gate_rec = store.find_node(run1.id, "position_gate", "default")
    assert gate_rec is not None and gate_rec.status == "needs_input"

    # Simulate the user confirming the position by overwriting the persisted content_jobs.
    define_rec = store.find_node(run1.id, "define_jobs", "default")
    assert define_rec is not None
    store.upsert_node(
        NodeRecord(
            id=define_rec.id,
            run_id=run1.id,
            node_name="define_jobs",
            idempotency_key="default",
            status="succeeded",
            output_json=json.dumps(items_payload([confirmed])),
        )
    )

    run2 = GraphRuntime(store, nodes()).run(run_id=run1.id)
    assert run2.state == "DRAFTED"
    gate_rec2 = store.find_node(run1.id, "position_gate", "default")
    assert gate_rec2 is not None and gate_rec2.status == "succeeded"


def test_define_jobs_strips_model_confirmed_so_gate_needs_input(tmp_path):
    """模型输出的 confirmed=true 必须被剥除：只有人类 confirm-position 才能放行。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    job = _job(job_id="j1", candidate_id="t1", position=_position(confirmed=True))
    runner = FakeJobsRunner([job])

    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner, jobs_repo=repo),
        make_position_gate_node(jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "NEEDS_INPUT"


def test_define_jobs_rejects_job_with_unknown_candidate(tmp_path):
    """candidate_id 必须存在于 match_results；否则过滤掉。"""
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    unknown = _job(job_id="j2", candidate_id="t_unknown", source_card_ids=("ev1",))
    runner = FakeJobsRunner([good, unknown])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    assert "j1" in rec.output_json
    assert "j2" not in rec.output_json


def test_define_jobs_rejects_cards_outside_own_candidate(tmp_path):
    """F5: reply job 的 source_card_ids 必须属于其自身候选的 match；original 可引用任意卡。"""
    card2 = _card().model_copy(update={"id": "ev2"})
    match2 = _match().model_copy(update={"candidate_id": "t2", "card_ids": ["ev2"]})

    bad = _job(job_id="j_bad", candidate_id="t1", source_card_ids=("ev1", "ev2"))
    good_original = _job(job_id="j_orig", candidate_id=None, source_card_ids=("ev2",))
    runner = FakeJobsRunner([bad, good_original])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results",
             seed=items_payload([_match(), match2])),
        Seed(name="extract_events", writes="evidence_cards",
             seed=items_payload([_card(), card2])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    assert "j_orig" in rec.output_json
    assert "j_bad" not in rec.output_json


def test_define_jobs_upserts_into_repo(tmp_path):
    """D7: define_jobs 将每个 job 写入 ContentJobRepository。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    job = _job(job_id="j1", candidate_id="t1", position=_position(confirmed=False))
    runner = FakeJobsRunner([job])

    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner, jobs_repo=repo),
    ]
    GraphRuntime(store, nodes).run()
    got = repo.get_job("j1")
    assert got is not None
    assert got.author_position is not None
    assert got.author_position.confirmed is False


def test_position_gate_reads_fresh_from_repo(tmp_path):
    """D7: gate 从 repo 取最新版本，用户确认后 context 里的旧版本不阻挡。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    unconfirmed = _job(job_id="j1", candidate_id=None, position=_position(confirmed=False))
    confirmed = _job(job_id="j1", candidate_id=None, position=_position(confirmed=True))
    repo.upsert_job(confirmed)

    node = make_position_gate_node(jobs_repo=repo)
    result = node.run({"content_jobs": items_payload([unconfirmed])})
    assert result.status == "succeeded"
    assert [j["id"] for j in result.output["items"]] == ["j1"]


def test_position_gate_falls_back_to_context_when_repo_missing(tmp_path):
    """D7: repo 查不到时回退到 context 里的 job。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    unconfirmed = _job(job_id="j1", candidate_id=None, position=_position(confirmed=False))

    node = make_position_gate_node(jobs_repo=repo)
    result = node.run({"content_jobs": items_payload([unconfirmed])})
    assert result.status == "needs_input"


def test_position_gate_asks_at_most_three_questions():
    """Task 2.4：primary 缺立场时只问 missing_questions（模型已限 ≤3 个）。"""
    node = make_position_gate_node()
    job = _job(job_id="j1", position=_position(confirmed=False)).model_copy(
        update={"missing_questions": ["q1", "q2", "q3"]}
    )
    result = node.run({"content_jobs": items_payload([job])})
    assert result.status == "needs_input"
    assert result.output["questions"] == ["q1", "q2", "q3"]
    assert [j["id"] for j in result.output["items"]] == ["j1"]


def test_position_gate_falls_back_once_after_reject(tmp_path):
    """用户拒绝 primary 后，确定性选择下一个 job（一次递补）。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    rejected = _job(
        job_id="A", candidate_id="t1", position=None, status=ContentJobStatus.DO_NOT_WRITE
    )
    fallback = _job(job_id="B", candidate_id="t1", position=_position(confirmed=False))
    repo.upsert_job(rejected)
    repo.upsert_job(fallback)

    node = make_position_gate_node(jobs_repo=repo)
    result = node.run({"content_jobs": items_payload([rejected, fallback])})
    assert result.status == "needs_input"
    assert [j["id"] for j in result.output["items"]] == ["B"]


def test_position_gate_stops_after_one_fallback(tmp_path):
    """primary 与一次递补都被拒后，不再提议第三个 job（避免无限循环）。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    rejected_a = _job(
        job_id="A", candidate_id="t1", position=None, status=ContentJobStatus.DO_NOT_WRITE
    )
    rejected_b = _job(
        job_id="B", candidate_id="t1", position=None, status=ContentJobStatus.DO_NOT_WRITE
    )
    active = _job(job_id="C", candidate_id=None, position=_position(confirmed=False))
    for job in (rejected_a, rejected_b, active):
        repo.upsert_job(job)

    node = make_position_gate_node(jobs_repo=repo)
    result = node.run(
        {"content_jobs": items_payload([rejected_a, rejected_b, active])}
    )
    assert result.status == "succeeded"
    assert result.output["items"] == []
    assert [d["job_id"] for d in result.output["deferred"]] == ["C"]


def test_position_gate_defers_others_and_persists_proposed(tmp_path):
    """其余 ready job 被 deferred 并以 PROPOSED 保存（不删除、不 do_not_write）。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    primary = _job(job_id="p", candidate_id="t1", position=_position(confirmed=True))
    secondary = _job(job_id="s", candidate_id=None, position=_position(confirmed=True))
    repo.upsert_job(primary)
    repo.upsert_job(secondary)

    node = make_position_gate_node(jobs_repo=repo)
    result = node.run({"content_jobs": items_payload([primary, secondary])})
    assert result.status == "succeeded"
    assert [j["id"] for j in result.output["items"]] == ["p"]
    assert [d["job_id"] for d in result.output["deferred"]] == ["s"]

    # secondary 被保存为 proposed（not now，可恢复），而非 do_not_write。
    saved = repo.get_job("s")
    assert saved is not None
    assert saved.status == ContentJobStatus.PROPOSED
    # primary 状态不变。
    assert repo.get_job("p").status == ContentJobStatus.READY


def test_draft_node_caps_replies_and_originals(tmp_path):
    """资源上限：replies ≤ max_daily_replies，originals ≤ max_daily_original_posts。"""
    jobs = [_job(job_id=f"r{i}", candidate_id="t1", source_card_ids=("ev1",)) for i in range(6)]
    jobs += [_job(job_id=f"o{i}", candidate_id=None, source_card_ids=("ev1",)) for i in range(3)]

    def write_reply(runner, match, candidate, cards_by_id, job):
        return _reply_draft().model_copy(update={"id": f"d_{job.id}"})

    def write_original(runner, cards, job):
        return _reply_draft().model_copy(
            update={"id": f"d_{job.id}", "kind": DraftKind.ORIGINAL, "candidate_id": None}
        )

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload(jobs)),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    replies = [d for d in drafts if d["kind"] == "reply"]
    originals = [d for d in drafts if d["kind"] == "original"]
    assert len(replies) == 5
    assert len(originals) == 1


def test_draft_node_bounds_write_attempts_to_cap(tmp_path):
    """cap 计尝试：即使 write 全部返回 None，写入次数也不得超出 daily cap。"""
    jobs = [_job(job_id=f"r{i}", candidate_id="t1", source_card_ids=("ev1",)) for i in range(10)]
    attempts: list[str] = []

    def write_reply(runner, match, candidate, cards_by_id, job):
        attempts.append(job.id)  # list.append 线程安全
        return None

    def write_original(runner, cards, job):
        return None

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload(jobs)),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    # 10 个 reply job，但只应尝试写入 max_daily_replies=5 次（而非全部 10 次）
    assert len(attempts) == 5


def test_draft_node_routes_on_recommended_format_not_candidate(tmp_path):
    """F7: recommended_format=ORIGINAL 但 candidate_id 非空时仍写 original。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    job = job.model_copy(update={"recommended_format": DraftKind.ORIGINAL})
    calls = {"reply": 0, "original": 0}

    def write_reply(runner, match, candidate, cards_by_id, job):
        calls["reply"] += 1
        return _reply_draft()

    def write_original(runner, cards, job):
        calls["original"] += 1
        return _reply_draft().model_copy(
            update={"id": "d2", "kind": DraftKind.ORIGINAL, "candidate_id": None}
        )

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload([job])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    assert len(drafts) == 1
    assert drafts[0]["kind"] == "original"
    assert calls == {"reply": 0, "original": 1}


def test_draft_node_runs_jobs_in_parallel_and_preserves_order(tmp_path):
    import threading
    import time

    # 串行实现会在第一个 job 上阻塞至 barrier 超时（BrokenBarrierError）；并行后两个
    # write 同时到达 barrier。job1 故意慢于 job2，但输出顺序仍应等于计划顺序。
    barrier = threading.Barrier(2, timeout=5)
    jobs = [
        _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",)),
        _job(job_id="job2", candidate_id="t1", source_card_ids=("ev1",)),
    ]

    def write_reply(runner, match, candidate, cards_by_id, job):
        barrier.wait()
        if job.id == "job1":
            time.sleep(0.05)
        return _reply_draft().model_copy(update={"id": f"d_{job.id}"})

    def write_original(runner, cards, job):
        return _reply_draft()

    store = _store(tmp_path)
    nodes = [
        Seed(name="gate", writes="ready_jobs", seed=items_payload(jobs)),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "draft", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    assert [d["id"] for d in drafts] == ["d_job1", "d_job2"]


def test_brief_body_follows_decision_first_order(tmp_path):
    """Task 3.1：Daily Brief 按计划 §3.1 的 10 段固定顺序渲染。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",)).model_copy(
        update={
            "core_message": "token bucket 是限制速率的最简方案",
            "why_now": "团队正在处理 agent 循环超时",
        }
    )
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        *_brief_seeds(
            drafts=items_payload([draft]),
            jobs=items_payload([job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([job]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)

    headers = [
        "## 1. 今日结论",
        "## 2. 今日首选",
        "## 3. 为什么值得说",
        "## 4. 作者判断与取舍",
        "## 5. 工程证据与讨论上下文",
        "## 6. 未解决风险",
        "## 7. 草稿正文",
        "## 8. 命令",
        "## 9. NOT NOW",
        "## 10. 本轮漏斗和轨道失败",
    ]
    positions = [body.index(h) for h in headers]
    assert positions == sorted(positions)
    assert "token bucket 是限制速率的最简方案" in body
    assert "团队正在处理 agent 循环超时" in body
    assert "use token bucket" in body
    assert "more memory" in body
    assert "token bucket rate limiting" in body
    assert "finch review approve" in body


def test_brief_primary_and_not_now_from_gate(tmp_path):
    """Task 3.1：今日首选来自 gate primary；NOT NOW 列出 deferred job 与理由。"""
    primary = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",)).model_copy(
        update={"core_message": "首选主题"}
    )
    deferred_job = _job(job_id="job2", candidate_id=None, source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})
    gate = items_payload([primary])
    gate["deferred"] = [{"job_id": "job2", "reason": "no external discussion context"}]

    nodes = [
        *_brief_seeds(
            drafts=items_payload([draft]),
            jobs=items_payload([primary, deferred_job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=gate,
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)

    assert "首选主题" in body
    not_now = body.split("## 9. NOT NOW", 1)[1].split("## 10.", 1)[0]
    assert "job2" in not_now
    assert "no external discussion context" in not_now


def test_brief_empty_result_explains_no_good_candidate(tmp_path):
    """Golden Case（Phase 0 延后项）：无草稿时 Brief 给出具体「不建议写」原因而非空白。"""
    rejected = _job(job_id="job1", status=ContentJobStatus.DO_NOT_WRITE, position=None).model_copy(
        update={"reject_reason": "not useful right now"}
    )
    nodes = [
        *_brief_seeds(
            jobs=items_payload([rejected]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)

    assert "今日没有建议发布的内容" in body
    assert "不建议写原因" in body
    assert "not useful right now" in body
    assert "提取证据卡：1 张" in body
    assert "收集讨论：1 条" in body
    assert "生成 jobs：1 个" in body
    assert "产出草稿：0 篇" in body


def test_brief_uses_fresh_job_from_repo(tmp_path):
    """F1：brief 从 repo 取 fresh job，resume 后采纳用户 confirm-position 的编辑。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    fresh = _job(
        job_id="job1",
        candidate_id="t1",
        position=_position(decision="fresh decision", confirmed=True),
    )
    stale = _job(
        job_id="job1",
        candidate_id="t1",
        position=_position(decision="stale decision", confirmed=False),
    )
    repo.upsert_job(fresh)
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        *_brief_seeds(
            drafts=items_payload([draft]),
            jobs=items_payload([stale]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([stale]),
        ),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "fresh decision" in body
    assert "stale decision" not in body


def test_brief_falls_back_to_context_job_when_repo_missing(tmp_path):
    """F1：repo 查不到 job 时回退到 context（gate/ready_jobs）里的 job。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    context_job = _job(
        job_id="job1",
        candidate_id="t1",
        position=_position(decision="context decision", confirmed=True),
    )
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        *_brief_seeds(
            drafts=items_payload([draft]),
            jobs=items_payload([context_job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([context_job]),
        ),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "context decision" in body


def test_brief_renders_critic_warnings_from_persisted_output(tmp_path):
    """Task 3.4：brief 从 critique 输出的结构化 draft_warnings 渲染「未解决风险」。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})
    drafts_payload = items_payload([draft])
    drafts_payload["draft_warnings"] = [
        DraftWarning(
            draft_id=draft.id, checker="critique", message="failed critique after 2 rewrites"
        ).model_dump(mode="json")
    ]

    nodes = [
        *_brief_seeds(
            drafts=drafts_payload,
            jobs=items_payload([job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([job]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)
    assert "failed critique after 2 rewrites" in body


def test_brief_renders_none_when_no_critic_warnings(tmp_path):
    """无未解决风险时「未解决风险」段渲染「无」。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        *_brief_seeds(
            drafts=items_payload([draft]),
            jobs=items_payload([job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([job]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)
    assert "## 6. 未解决风险\n无" in body


def test_brief_binds_warnings_to_draft(tmp_path):
    """Task 3.4：警告按 draft_id 归属，绝不作为全局列表出现在每个候选上。"""
    draft_1 = _reply_draft().model_copy(update={"id": "1"})
    draft_10 = _reply_draft().model_copy(update={"id": "10"})
    drafts_payload = items_payload([draft_1, draft_10])
    drafts_payload["draft_warnings"] = [
        DraftWarning(
            draft_id="10", checker="evidence", message="rejected by evidence (claim[0])"
        ).model_dump(mode="json")
    ]

    nodes = [
        *_brief_seeds(
            drafts=drafts_payload,
            cards=items_payload([_card()]),
            matches=items_payload([_match()]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)
    # primary = draft "1"；其「未解决风险」段不得出现 draft "10" 的警告。
    assert "## 6. 未解决风险\n无" in body
    # draft "10" 的警告只出现在其自身区块。
    assert "rejected by evidence" in body.split("[草稿 10]", 1)[1]


def test_brief_legacy_string_warnings_still_render(tmp_path):
    """向后兼容：旧版 ``warnings`` 字符串仍被解析并绑定到所属草稿。"""
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})
    drafts_payload = items_payload([draft])
    drafts_payload["warnings"] = [f"draft {draft.id}: failed critique after 2 rewrites"]
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))

    nodes = [
        *_brief_seeds(
            drafts=drafts_payload,
            jobs=items_payload([job]),
            cards=items_payload([_card()]),
            candidates=items_payload([_candidate()]),
            matches=items_payload([_match()]),
            gate=items_payload([job]),
        ),
        make_brief_node(QualityGates()),
    ]
    _, payload = _run_brief(tmp_path, nodes)
    body = _brief_body(payload)
    assert "failed critique after 2 rewrites" in body


def test_brief_uses_real_run_id(tmp_path):
    """Task 3.1：brief 使用 node context 提供的真实 run id，而非硬编码 "daily"。"""
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    node = make_brief_node(QualityGates())
    result = node.run(
        {
            "run_id": "abc-123",
            "drafts": items_payload([draft]),
            "content_jobs": items_payload([job]),
            "evidence_cards": items_payload([_card()]),
            "ready_jobs": items_payload([job]),
            "candidates": items_payload([_candidate()]),
            "match_results": items_payload([_match()]),
        }
    )
    assert result.status == "succeeded"
    assert result.output["items"][0]["run_id"] == "abc-123"


def test_default_checker_suite_has_eight_checkers():
    """Task 6: 默认检查器套件 = 现有 4 个 + 新增 4 个。"""
    suite = default_checker_suite(CodexRunner())
    assert [c.name for c in suite] == [
        "evidence",
        "decision",
        "specificity",
        "portability",
        "voice",
        "structure",
        "actionability",
        "safety",
    ]


def test_critique_node_needs_input_via_safety_checker(tmp_path):
    """Task 6: SafetyChecker 设置 requires_human_input → needs_input 分支可达。"""
    from finch.content.checkers.safety import SafetyChecker

    draft = _reply_draft().model_copy(
        update={"body": "my token is ghp_abcdefghijklmnopqrstuvwxyz123"}
    )

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft

    node = make_critique_node(
        CodexRunner(), rewrite, QualityGates(), checkers=[SafetyChecker()]
    )
    result = node.run(
        {
            "drafts": items_payload([draft]),
            "match_results": items_payload([_match()]),
            "evidence_cards": items_payload([_card()]),
        }
    )
    assert result.status == "needs_input"
    assert any("safety" in w for w in result.warnings)


def test_define_jobs_node_short_circuits_without_cards(tmp_path):
    """Phase 0 回归：无证据卡时 define_jobs 节点不调用 runner，即使 match_results 非空。"""
    runner = FakeJobsRunner([])  # run 被调用会返回空，但这里应当根本不被调用
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    assert runner.calls == 0
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    assert json.loads(rec.output_json)["items"] == []


def test_define_jobs_expands_in_parallel_and_preserves_order(tmp_path):
    import threading
    import time

    # 串行实现会在第一个 expand 上阻塞至 barrier 超时（BrokenBarrierError）；并行后两个
    # expand 同时到达 barrier。job1 故意慢于 job2，但输出顺序仍应等于 plan 顺序。
    barrier = threading.Barrier(2, timeout=5)
    jobs = [
        _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",)),
        _job(job_id="job2", candidate_id="t1", source_card_ids=("ev1",)),
    ]

    class BarrierJobsRunner(FakeJobsRunner):
        def run(self, prompt, output_model, **kw):
            if output_model is ContentJob:
                barrier.wait()
                match = re.search(r'"id"\s*:\s*"(tp\d+)"', prompt)
                idx = int(match.group(1)[2:])
                if idx == 0:
                    time.sleep(0.05)
                return self.jobs[idx]
            return super().run(prompt, output_model, **kw)

    runner = BarrierJobsRunner(jobs)
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    output_jobs = json.loads(rec.output_json)["items"]
    assert [j["id"] for j in output_jobs] == ["job1", "job2"]


def test_define_jobs_isolates_topic_expand_failure(tmp_path):
    """单个 topic 展开失败（如 JSON 截断）被隔离，其余 topic 的合法 job 照常产出。"""
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    bad = _job(job_id="j2", candidate_id="t1", source_card_ids=("ev1",))

    class PartialFailRunner(FakeJobsRunner):
        def run(self, prompt, output_model, **kw):
            if output_model is ContentJob:
                match = re.search(r'"id"\s*:\s*"(tp\d+)"', prompt)
                if match and match.group(1) == "tp1":
                    raise RuntimeError("boom")
            return super().run(prompt, output_model, **kw)

    runner = PartialFailRunner([good, bad])
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["j1"]
    assert any("expand failed" in w for w in payload.get("warnings", []))


def test_define_jobs_dedups_duplicate_job_ids(tmp_path):
    """重复 job id（重复/重叠主题）在去重后只保留一个，避免 upsert 冲突与下游重复列表。"""
    dupe1 = _job(job_id="dup", candidate_id="t1", source_card_ids=("ev1",))
    dupe2 = _job(job_id="dup", candidate_id="t1", source_card_ids=("ev1",))
    runner = FakeJobsRunner([dupe1, dupe2])
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["dup"]


def test_original_flow_confirmed_position_produces_draft(tmp_path):
    """Phase 0 回归：有证据卡 + 已确认立场 → job 定义 → draft 产出 → 进入人工审核。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    # 人工 confirm-position 后 repo 中为 confirmed 版本；模型输出会被剥除 confirmed。
    repo.upsert_job(
        _job(job_id="job1", candidate_id="t1", position=_position(confirmed=True))
    )
    runner = FakeJobsRunner(
        [_job(job_id="job1", candidate_id="t1", position=_position(confirmed=False))]
    )

    def write_reply(runner, match, candidate, cards_by_id, job):
        assert job is not None
        return _reply_draft().model_copy(update={"content_job_id": job.id})

    def write_original(runner, cards, job):
        raise AssertionError("original writer must not be called for a REPLY job")

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        raise AssertionError("rewrite must not be called when the draft passes")

    checker = SeqChecker([_pass_check()])

    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner, runner),  # 不传 repo，避免覆盖已确认的 job
        make_position_gate_node(jobs_repo=repo),
        make_draft_node(CodexRunner(), write_reply, write_original, QualityGates()),
        make_critique_node(
            CodexRunner(), rewrite, QualityGates(), checkers=[checker]
        ),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"

    draft_rec = store.find_node(run.id, "draft", "default")
    assert draft_rec is not None
    assert "d1" in draft_rec.output_json
    assert "job1" in draft_rec.output_json


