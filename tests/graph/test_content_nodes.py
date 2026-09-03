"""Writer/Critic/Daily Brief 节点 7–9 测试（Phase 5 Task F6）。"""

import json

from finch.codex.runner import CodexRunner
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobsOutput,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import ClaimRef, Draft, DraftKind
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


def test_brief_node_terminal_state(tmp_path):
    # 无稿 → COMPLETED
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(_store(tmp_path), nodes).run()
    assert run.state == "COMPLETED"


def test_brief_node_waiting_when_drafts(tmp_path):
    from finch.content.models import Draft, DraftKind

    d = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t", body="hi", claims=[])
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([d])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([])),
        make_brief_node(QualityGates()),
    ]
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
        return ContentJobsOutput(items=self.jobs)


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


def test_define_jobs_node_produces_and_filters_jobs(tmp_path):
    good = _job(job_id="j1", candidate_id="t1", source_card_ids=("ev1",))
    bad = _job(job_id="j2", candidate_id=None, source_card_ids=("ev1", "ev_999"))
    runner = FakeJobsRunner([good, bad])

    store = _store(tmp_path)
    nodes = [
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        Seed(name="collect_tweets", writes="candidates", seed=items_payload([_candidate()])),
        make_define_jobs_node(runner),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "JOBS_DEFINED"
    rec = store.find_node(run.id, "define_jobs", "default")
    assert rec is not None
    assert "j1" in rec.output_json
    assert "j2" not in rec.output_json
    assert runner.calls == 1


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


def test_position_gate_mixed_routes_ready_and_unready():
    node = make_position_gate_node()
    ready = _job(job_id="j1", candidate_id="t1", position=_position(confirmed=True))
    unconfirmed = _job(job_id="j2", candidate_id=None, position=_position(confirmed=False))
    do_not_write = _job(
        job_id="j3", status=ContentJobStatus.DO_NOT_WRITE, position=None
    )
    result = node.run(
        {"content_jobs": items_payload([ready, unconfirmed, do_not_write])}
    )
    assert result.status == "needs_input"
    # only the unready job is reported; DO_NOT_WRITE and ready jobs are excluded
    assert [j["id"] for j in result.output["items"]] == ["j2"]


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
        make_define_jobs_node(runner, jobs_repo=repo),
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
        make_define_jobs_node(runner),
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
        make_define_jobs_node(runner),
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
        make_define_jobs_node(runner, jobs_repo=repo),
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


def test_brief_renders_six_items_per_candidate(tmp_path):
    """D10: Daily Brief 每个候选渲染 Spec §7 的 6 项。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([draft])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([job])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    briefs = json.loads(rec.output_json)["items"]
    assert briefs
    body = briefs[0]["body"]
    for marker in (
        "要完成的工作",
        "目标读者与期望动作",
        "证据来源",
        "核心判断与取舍",
        "Critic 未解决风险",
        "推荐动作",
    ):
        assert marker in body, marker
    assert "readers don't know how to rate limit" in body
    assert "backend engineers" in body
    assert "token bucket rate limiting" in body
    assert "use token bucket" in body
    assert "more memory" in body


def test_brief_recommended_action_uses_fresh_job_from_repo(tmp_path):
    """F1: brief 从 repo 取 fresh job，resume 后已确认的 job 不再提示「确认观点」。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    confirmed = _job(job_id="job1", candidate_id="t1", position=_position(confirmed=True))
    stale = _job(job_id="job1", candidate_id="t1", position=_position(confirmed=False))
    repo.upsert_job(confirmed)
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([draft])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([stale])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "WAITING_FOR_REVIEW"
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "待人工审核（发布需手动）" in body
    assert "确认观点" not in body


def test_brief_recommended_action_needs_input_when_unconfirmed(tmp_path):
    """F1: 未确认（needs_input）的 job 仍提示「确认观点」。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    unconfirmed = _job(job_id="job1", candidate_id="t1", position=_position(confirmed=False))
    repo.upsert_job(unconfirmed)
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([draft])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([unconfirmed])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "确认观点" in body


def test_brief_falls_back_to_context_job_when_repo_missing(tmp_path):
    """F1: repo 查不到 job 时回退到 context 里的 job。"""
    store = _store(tmp_path)
    repo = ContentJobRepository(store)
    confirmed = _job(job_id="job1", candidate_id="t1", position=_position(confirmed=True))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([draft])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([confirmed])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates(), jobs_repo=repo),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "待人工审核（发布需手动）" in body


def test_brief_renders_critic_warnings_from_persisted_output(tmp_path):
    """F2: brief 从 persisted critique 输出读取 warnings 渲染第 5 项。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})
    drafts_payload = items_payload([draft])
    drafts_payload["warnings"] = [f"draft {draft.id}: failed critique after 2 rewrites"]

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=drafts_payload),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([job])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "failed critique after 2 rewrites" in body


def test_brief_renders_none_when_no_critic_warnings(tmp_path):
    """F2: 无 warnings 时第 5 项渲染「无」。"""
    job = _job(job_id="job1", candidate_id="t1", source_card_ids=("ev1",))
    draft = _reply_draft().model_copy(update={"content_job_id": "job1"})

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([draft])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([job])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    body = json.loads(rec.output_json)["items"][0]["body"]
    assert "Critic 未解决风险：无" in body


def test_brief_does_not_prefix_match_draft_ids(tmp_path):
    """F6: draft 1 的 brief 不应继承 draft 10 的 warning。"""
    draft_1 = _reply_draft().model_copy(update={"id": "1"})
    draft_10 = _reply_draft().model_copy(update={"id": "10"})
    drafts_payload = items_payload([draft_1, draft_10])
    drafts_payload["warnings"] = ["draft 10: rejected by evidence (claim[0])"]

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=drafts_payload),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="define_jobs", writes="content_jobs", seed=items_payload([])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_brief_node(QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    rec = store.find_node(run.id, "brief", "default")
    assert rec is not None
    briefs = json.loads(rec.output_json)["items"]
    assert len(briefs) == 1
    sections = briefs[0]["body"].split("## 候选 ")
    block_1 = next(s for s in sections if s.startswith("1\n"))
    block_10 = next(s for s in sections if s.startswith("10\n"))
    assert "rejected by evidence" not in block_1
    assert "rejected by evidence" in block_10


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


