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
    make_select_node,
    make_write_node,
)
from finch.graph.context import items_payload, parse_items
from finch.graph.events import NodeResult
from finch.graph.nodes import Node
from finch.graph.runtime import GraphRuntime
from finch.settings import QualityGates
from finch.storage.database import Store
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


def _position(decision="use token bucket", tradeoff="more memory"):
    return AuthorPosition(
        claim="token bucket is the right call",
        decision=decision,
        tradeoff=tradeoff,
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


def _never_rewrite(runner, draft, failed_checks, cards_by_id, job=None):
    raise AssertionError("rewrite must not be called")


def test_write_node_writes_reply_and_original(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert "d1" in rec.output_json and "d2" in rec.output_json


def test_write_node_empty_ready_jobs_writes_empty(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") == '{"items":[]}'
    assert calls == {"reply": 0, "original": 0}


def test_write_node_sets_run_id():
    """Task 4：write 节点把 ctx 里的 run_id 打到每条产出的 Draft 上。"""
    job = _job(job_id="j1", candidate_id=None, source_card_ids=("ev1",))

    def write_reply(runner, match, candidate, cards_by_id, job):
        raise AssertionError("reply writer must not be called for an ORIGINAL job")

    def write_original(runner, cards, job):
        return Draft(
            id="d1",
            kind=DraftKind.ORIGINAL,
            body="hi",
            content_job_id=job.id,
            claims=[
                ClaimRef(
                    statement="x", evidence_card_id="ev1", confidence=ClaimConfidence.VERIFIED
                )
            ],
        )

    node = make_write_node(None, write_reply, write_original, _never_rewrite, QualityGates())
    ctx = {
        "ready_jobs": items_payload([job]),
        "evidence_cards": items_payload([_card()]),
        "candidates": items_payload([]),
        "match_results": items_payload([]),
        "run_id": "r1",
    }
    result = node.run(ctx)
    drafts = parse_items(result.output, Draft)
    assert drafts and drafts[0].run_id == "r1"


def test_write_node_skips_l1_when_l0_passes_and_mode_not_always():
    """L0 通过且 mode != always：不跑 L1（checker 不被调用），草稿原样保留。"""
    checker = SeqChecker([_pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        raise AssertionError("rewrite must not be called when L1 is skipped")

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: _reply_draft(),
        lambda r, cards, job: None,
        rewrite,
        QualityGates(),  # llm_critique_mode = "on_fail_or_gate"
        checkers=[checker],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert checker.calls == 0
    drafts = parse_items(result.output, Draft)
    assert [d.id for d in drafts] == ["d1"]
    assert result.output.get("reports") == []


def test_write_node_runs_l1_when_mode_always():
    checker = SeqChecker([_pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        raise AssertionError("rewrite must not be called when the checker passes")

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: _reply_draft(),
        lambda r, cards, job: None,
        rewrite,
        QualityGates(llm_critique_mode="always"),
        checkers=[checker],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert checker.calls == 1
    drafts = parse_items(result.output, Draft)
    assert [d.id for d in drafts] == ["d1"]


def test_write_node_runs_l1_when_l0_fails():
    invalid = _reply_draft().model_copy(
        update={
            "claims": [
                ClaimRef(
                    statement="x", evidence_card_id="ev_999", confidence=ClaimConfidence.VERIFIED
                )
            ]
        }
    )
    checker = SeqChecker([_pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        raise AssertionError("rewrite must not be called when the checker passes")

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: invalid,
        lambda r, cards, job: None,
        rewrite,
        QualityGates(),
        checkers=[checker],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert checker.calls == 1


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


def _write_ctx(*, job=None):
    """write 节点 run 的直接 ctx（reply job + candidate + match + card）。"""
    return {
        "ready_jobs": items_payload(
            [job or _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))]
        ),
        "evidence_cards": items_payload([_card()]),
        "candidates": items_payload([_candidate()]),
        "match_results": items_payload([_match()]),
    }


def _write_seed_nodes(
    rewrite,
    checker=None,
    gates=None,
    jobs=None,
    write_reply_fn=None,
    write_original_fn=None,
    matches=None,
):
    """write 节点上游 reads 的 Seed + write 节点（默认 reply job + always 模式）。"""
    jobs = jobs if jobs is not None else [
        _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    ]
    matches = matches if matches is not None else [_match()]

    def default_write_reply(runner, match, candidate, cards_by_id, job):
        return _reply_draft()

    def default_write_original(runner, cards, job):
        raise AssertionError("original writer must not be called for a REPLY job")

    return [
        Seed(name="gate", writes="ready_jobs", seed=items_payload(jobs)),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload(matches)),
        make_write_node(
            CodexRunner(),
            write_reply_fn if write_reply_fn is not None else default_write_reply,
            write_original_fn if write_original_fn is not None else default_write_original,
            rewrite,
            gates or QualityGates(llm_critique_mode="always"),
            checkers=[checker] if checker is not None else None,
        ),
    ]


def test_write_node_rewrites_until_pass(tmp_path):
    calls = {"rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "v2"})
    checker = SeqChecker([_failed_check(), _pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _write_seed_nodes(rewrite, checker=checker)).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert "v2" in rec.output_json
    assert calls == {"rewrite": 1}
    assert checker.calls == 2


def test_write_node_drops_unfixable_draft(tmp_path):
    checker = SeqChecker([_failed_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft.model_copy(update={"body": "v2"})

    store = _store(tmp_path)
    run = GraphRuntime(
        store,
        _write_seed_nodes(
            rewrite,
            checker=checker,
            gates=QualityGates(llm_critique_mode="always", max_rewrite_rounds=2),
        ),
    ).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert json.loads(rec.output_json)["items"] == []
    assert any("failed critique" in w for w in json.loads(rec.output_json)["warnings"])


def test_write_node_keeps_draft_fixed_by_single_rewrite(tmp_path):
    calls = {"rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})
    checker = SeqChecker([_failed_check(), _pass_check()])

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(
        store,
        _write_seed_nodes(
            rewrite,
            checker=checker,
            gates=QualityGates(llm_critique_mode="always", max_rewrite_rounds=1),
        ),
    ).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"rewrite": 1}


def test_write_node_keeps_draft_fixed_by_second_rewrite(tmp_path):
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
        _write_seed_nodes(
            rewrite,
            checker=checker,
            gates=QualityGates(llm_critique_mode="always", max_rewrite_rounds=2),
        ),
    ).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"rewrite": 2}
    assert checker.calls == 3


def test_write_node_warns_on_invalid_rewritten_claims():
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

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: _reply_draft(),
        lambda r, cards, job: None,
        rewrite,
        QualityGates(llm_critique_mode="always"),
        checkers=[checker],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert result.output["items"] == []
    assert any("invalid claims" in w for w in result.warnings)
    # F2: warnings are also embedded in the persisted output (runtime only persists output).
    assert any("invalid claims" in w for w in result.output["warnings"])


def test_write_node_drops_hard_fail_draft(tmp_path):
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
    run = GraphRuntime(store, _write_seed_nodes(rewrite, checker=checker)).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    assert payload["items"] == []
    assert any("rejected" in w and "evidence" in w for w in payload["warnings"])
    # hard_fail is dropped immediately, never rewritten
    assert calls == {"rewrite": 0}


def test_write_node_emits_draft_warnings(tmp_path):
    """Task 3.4：write 输出结构化 draft_warnings（draft_id/checker/message 绑定）。"""
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
    run = GraphRuntime(store, _write_seed_nodes(rewrite, checker=checker)).run()
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    payload = json.loads(rec.output_json)
    draft_warnings = [DraftWarning.model_validate(w) for w in payload["draft_warnings"]]
    assert [w.draft_id for w in draft_warnings] == ["d1"]
    assert draft_warnings[0].checker == "evidence"
    assert "rejected by evidence" in draft_warnings[0].message


def test_write_node_records_needs_input_warning():
    """needs_input 不再停图：记 warning，草稿丢弃，节点仍 succeeded。"""
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

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: _reply_draft(),
        lambda r, cards, job: None,
        rewrite,
        QualityGates(llm_critique_mode="always"),
        checkers=[checker],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert result.output["items"] == []
    assert any("decision" in w and "human input" in w for w in result.warnings)


def test_write_node_passes_only_failed_checks_to_rewrite(tmp_path):
    captured: list[list[CheckResult]] = []
    checker = SeqChecker([_failed_check(), _pass_check()])
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        captured.append(failed_checks)
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _write_seed_nodes(rewrite, checker=checker)).run()
    assert run.state == "DRAFTED"
    assert len(captured) == 1
    assert [c.checker for c in captured[0]] == ["specificity"]
    assert all(not c.passed for c in captured[0])


def test_write_node_emits_per_round_reports(tmp_path):
    checker = SeqChecker([_failed_check(), _pass_check()])
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return fixed

    store = _store(tmp_path)
    run = GraphRuntime(store, _write_seed_nodes(rewrite, checker=checker)).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    reports = json.loads(rec.output_json)["reports"]
    assert len(reports) == 2
    assert reports[0]["draft_id"] == "d1"
    assert reports[0]["round"] == 0
    assert reports[0]["checks"] == [_failed_check().model_dump(mode="json")]
    assert reports[0]["outcome"] == "rewrite"
    assert reports[0]["version"]["body"] == "hi"
    assert reports[1]["round"] == 1
    assert reports[1]["outcome"] == "pass"
    assert reports[1]["version"]["body"] == "fixed"


def test_write_node_runs_checkers_in_parallel(tmp_path):
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
        Seed(name="gate", writes="ready_jobs",
             seed=items_payload([_job(job_id="job1", candidate_id="t1",
                                      source_card_ids=("ev1",))])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        make_write_node(
            CodexRunner(),
            lambda r, m, c, cards, job: _reply_draft(),
            lambda r, cards, job: None,
            rewrite,
            QualityGates(llm_critique_mode="always"),
            checkers=checkers,
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"


def test_select_node_produces_and_filters_jobs(tmp_path):
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    bad = _job(job_id="j2", candidate_id=None, source_card_ids=("ev1", "ev_999"))
    runner = FakeJobsRunner([good, bad])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    rec = store.find_node(run.id, "select", "default")
    assert rec is not None
    assert "j1" in rec.output_json
    assert "j2" not in rec.output_json
    assert runner.calls == 2  # 1 plan + 1 expand（bad 主题在预过滤阶段即被剔除）


def test_select_node_rejects_job_with_unknown_candidate(tmp_path):
    """candidate_id 必须存在于 match_results；否则过滤掉。"""
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    unknown = _job(job_id="j2", candidate_id="t_unknown", source_card_ids=("ev1",))
    runner = FakeJobsRunner([good, unknown])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    rec = store.find_node(run.id, "select", "default")
    assert rec is not None
    assert "j1" in rec.output_json
    assert "j2" not in rec.output_json


def test_select_node_rejects_cards_outside_own_candidate(tmp_path):
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
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    rec = store.find_node(run.id, "select", "default")
    assert rec is not None
    assert "j_orig" in rec.output_json
    assert "j_bad" not in rec.output_json


def test_select_node_upserts_into_repo(tmp_path):
    """select 将每个合法 job 写入 ContentJobRepository。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    job = _job(job_id="j1", candidate_id="t1", position=_position())
    runner = FakeJobsRunner([job])

    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, jobs_repo=repo, gates=QualityGates()),
    ]
    GraphRuntime(store, nodes).run()
    got = repo.get_job("j1")
    assert got is not None
    assert got.author_position is not None
    assert got.author_position.decision == "use token bucket"


def test_select_node_expands_only_top_k(tmp_path):
    """先选后写：只展开 Top K（默认 1）个主题，其余主题标题进 unexpanded_topics。"""
    job1 = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    job2 = _job(job_id="j2", candidate_id="t1", source_card_ids=("ev1",))
    runner = FakeJobsRunner([job1, job2])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),  # max_daily_original_posts=1
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    rec = store.find_node(run.id, "select", "default")
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["j1"]
    assert len(payload["unexpanded_topics"]) == 1
    assert runner.calls == 2  # 1 plan + 1 expand（只展开排序后的第一个主题）


def test_select_node_sorts_reply_before_original(tmp_path):
    """排序：有讨论上下文（candidate_id 非空）的主题优先于 original。"""
    original = _job(job_id="j_orig", candidate_id=None, source_card_ids=("ev1",))
    reply = _job(job_id="j_reply", candidate_id="t1", source_card_ids=("ev1",))
    # 故意把 original 放前面：若忽略排序，稳定顺序会让 original 胜出。
    runner = FakeJobsRunner([original, reply])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "select", "default")
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["j_reply"]


def test_select_node_falls_back_once_on_expand_failure(tmp_path):
    """展开失败递补下一个主题（最多一次）。"""
    first = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    second = _job(job_id="j2", candidate_id="t1", source_card_ids=("ev1",))

    class PartialFailRunner(FakeJobsRunner):
        def run(self, prompt, output_model, **kw):
            if output_model is ContentJob:
                match = re.search(r'"id"\s*:\s*"(tp\d+)"', prompt)
                if match and match.group(1) == "tp0":
                    raise RuntimeError("boom")
            return super().run(prompt, output_model, **kw)

    runner = PartialFailRunner([first, second])
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "select", "default")
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["j2"]
    assert any("expand failed" in w for w in payload.get("warnings", []))


def test_select_node_dedups_duplicate_job_ids(tmp_path):
    """重复 job id（重复/重叠主题）在去重后只保留一个，避免 upsert 冲突与下游重复列表。"""
    dupe1 = _job(job_id="dup", candidate_id="t1", source_card_ids=("ev1",))
    dupe2 = _job(job_id="dup", candidate_id="t1", source_card_ids=("ev1",))
    runner = FakeJobsRunner([dupe1, dupe2])
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates(max_daily_original_posts=2)),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    rec = store.find_node(run.id, "select", "default")
    payload = json.loads(rec.output_json)
    assert [j["id"] for j in payload["items"]] == ["dup"]


def test_write_node_caps_replies_and_originals(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    replies = [d for d in drafts if d["kind"] == "reply"]
    originals = [d for d in drafts if d["kind"] == "original"]
    assert len(replies) == 5
    assert len(originals) == 1


def test_write_node_bounds_write_attempts_to_cap(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    # 10 个 reply job，但只应尝试写入 max_daily_replies=5 次（而非全部 10 次）
    assert len(attempts) == 5


def test_write_node_routes_on_recommended_format_not_candidate(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    assert len(drafts) == 1
    assert drafts[0]["kind"] == "original"
    assert calls == {"reply": 0, "original": 1}


def test_write_node_runs_jobs_in_parallel_and_preserves_order(tmp_path):
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
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        make_write_node(CodexRunner(), write_reply, write_original, _never_rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"
    rec = store.find_node(run.id, "write", "default")
    assert rec is not None
    drafts = json.loads(rec.output_json)["items"]
    assert [d["id"] for d in drafts] == ["d_job1", "d_job2"]


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


def test_write_node_needs_input_via_safety_checker():
    """Task 6: SafetyChecker 设置 requires_human_input → needs_input 记 warning，不停图。"""
    from finch.content.checkers.safety import SafetyChecker

    draft = _reply_draft().model_copy(
        update={"body": "my token is ghp_abcdefghijklmnopqrstuvwxyz123"}
    )

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        return draft

    node = make_write_node(
        CodexRunner(),
        lambda r, m, c, cards, job: draft,
        lambda r, cards, job: None,
        rewrite,
        QualityGates(llm_critique_mode="always"),
        checkers=[SafetyChecker()],
    )
    result = node.run(_write_ctx())
    assert result.status == "succeeded"
    assert any("safety" in w for w in result.warnings)


def test_select_node_short_circuits_without_cards(tmp_path):
    """Phase 0 回归：无证据卡时 select 节点不调用 runner，即使 match_results 非空。"""
    runner = FakeJobsRunner([])  # run 被调用会返回空，但这里应当根本不被调用
    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_SELECTED"
    assert runner.calls == 0
    rec = store.find_node(run.id, "select", "default")
    assert rec is not None
    assert json.loads(rec.output_json)["items"] == []


def test_original_flow_complete_position_produces_draft(tmp_path):
    """Phase 0 回归：有证据卡 → select 选 job → write 产出草稿 → 进入人工审核队列。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    repo.upsert_job(
        _job(job_id="job1", candidate_id="t1", position=_position())
    )
    runner = FakeJobsRunner(
        [_job(job_id="job1", candidate_id="t1", position=_position())]
    )

    def write_reply(runner, match, candidate, cards_by_id, job):
        assert job is not None
        return _reply_draft().model_copy(update={"content_job_id": job.id})

    def write_original(runner, cards, job):
        raise AssertionError("original writer must not be called for a REPLY job")

    def rewrite(runner, draft, failed_checks, cards_by_id, job=None):
        raise AssertionError("rewrite must not be called when the draft passes")

    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_select_node(runner, runner, gates=QualityGates()),
        make_write_node(CodexRunner(), write_reply, write_original, rewrite, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "DRAFTED"

    write_rec = store.find_node(run.id, "write", "default")
    assert write_rec is not None
    assert "d1" in write_rec.output_json
    assert "job1" in write_rec.output_json



