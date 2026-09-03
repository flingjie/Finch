"""Writer/Critic/Daily Brief 节点 7–9 测试（Phase 5 Task F6）。"""

import json

from finch.codex.runner import CodexRunner
from finch.content.critic import CritiqueResult
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


def test_critique_node_rewrites_until_pass(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "v2"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] == 1:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(CodexRunner(), rewrite, critique, QualityGates()),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "v2" in rec.output_json
    assert calls == {"critique": 2, "rewrite": 1}


def test_critique_node_drops_unfixable_draft(tmp_path):
    def critique(runner, draft, cards_by_id):
        return CritiqueResult(passed=False, quality_score=0.5)

    def rewrite(runner, draft, critique_result, cards_by_id):
        return draft.model_copy(update={"body": "v2"})

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=2)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert rec.output_json.replace(" ", "") == '{"items":[]}'


def test_critique_node_keeps_draft_fixed_by_single_rewrite(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] == 1:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=1)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"critique": 2, "rewrite": 1}


def test_critique_node_keeps_draft_fixed_by_second_rewrite(tmp_path):
    calls = {"critique": 0, "rewrite": 0}
    fixed = _reply_draft().model_copy(update={"body": "fixed"})

    def critique(runner, draft, cards_by_id):
        calls["critique"] += 1
        if calls["critique"] < 3:
            return CritiqueResult(passed=False, quality_score=0.5)
        return CritiqueResult(passed=True, quality_score=0.8)

    def rewrite(runner, draft, critique_result, cards_by_id):
        calls["rewrite"] += 1
        if calls["rewrite"] < 2:
            return draft
        return fixed

    store = _store(tmp_path)
    nodes = [
        Seed(name="draft", writes="drafts", seed=items_payload([_reply_draft()])),
        Seed(name="match_evidence", writes="match_results", seed=items_payload([_match()])),
        Seed(name="extract_events", writes="evidence_cards", seed=items_payload([_card()])),
        make_critique_node(
            CodexRunner(), rewrite, critique, QualityGates(max_rewrite_rounds=2)
        ),
    ]
    run = GraphRuntime(store, nodes).run()
    assert run.state == "CRITIQUED"
    rec = store.find_node(run.id, "critique", "default")
    assert rec is not None
    assert "fixed" in rec.output_json
    assert calls == {"critique": 3, "rewrite": 2}


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

    def critique(runner, draft, cards_by_id):
        return CritiqueResult(passed=False, quality_score=0.5)

    def rewrite(runner, draft, critique_result, cards_by_id):
        return invalid

    node = make_critique_node(CodexRunner(), rewrite, critique, QualityGates())
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

