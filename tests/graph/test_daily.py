"""每日 Graph 与 Runtime 集成测试（Phase 4 Task D1）。"""

from finch.codex.runner import CodexRunner
from finch.evidence.extractor import Extractor
from finch.github.gh_client import GhClient
from finch.graph.daily import daily_nodes
from finch.settings import Settings
from finch.storage.database import Store
from finch.storage.repositories import ContentJobRepository
from finch.twitter.opencli_client import OpenCliClient


def test_daily_nodes_has_eleven_nodes(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    nodes = daily_nodes(
        settings=Settings(repositories=["flingjie/FDE-Gym"]),
        store=store,
        gh=GhClient(),
        opencli=OpenCliClient(),
        extractor=Extractor(CodexRunner()),
        runner=CodexRunner(),
        commits_by_repo={"flingjie/FDE-Gym": []},
        known_commit_urls=set(),
        repo_is_private={"flingjie/FDE-Gym": False},
    )
    assert [n.name for n in nodes] == [
        "preflight",
        "sync_commits",
        "extract_events",
        "collect_tweets",
        "recall",
        "match_evidence",
        "define_jobs",
        "position_gate",
        "draft",
        "critique",
        "brief",
    ]
    assert nodes[6].reads == ["match_results", "evidence_cards", "candidates"]
    assert nodes[7].reads == ["content_jobs"]
    assert nodes[8].reads == ["ready_jobs", "evidence_cards", "candidates"]
    assert nodes[10].writes == "brief"
    assert nodes[10].terminal_state_key == "terminal_state"


def test_daily_nodes_order_and_contract(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    nodes = daily_nodes(
        settings=Settings(repositories=["flingjie/FDE-Gym"]),
        store=store,
        gh=GhClient(),
        opencli=OpenCliClient(),
        extractor=Extractor(CodexRunner()),
        runner=CodexRunner(),
        commits_by_repo={"flingjie/FDE-Gym": []},
        known_commit_urls=set(),
        repo_is_private={"flingjie/FDE-Gym": False},
    )
    assert [n.name for n in nodes] == [
        "preflight", "sync_commits", "extract_events",
        "collect_tweets", "recall", "match_evidence",
        "define_jobs", "position_gate", "draft", "critique", "brief",
    ]
    assert nodes[4].reads == ["candidates", "evidence_cards"]
    assert nodes[5].writes == "match_results"
    assert nodes[5].reads == ["ranked_candidates", "evidence_cards", "candidates"]
    assert nodes[6].writes == "content_jobs"
    assert nodes[7].writes == "ready_jobs"
    assert nodes[9].reads == [
        "drafts", "match_results", "evidence_cards", "content_jobs", "ready_jobs",
    ]


def test_daily_runtime_full_pipeline_and_hydration(tmp_path, monkeypatch):
    from finch.content.checkers.actionability import _ActionabilityOutput
    from finch.content.checkers.decision import _DecisionOutput
    from finch.content.checkers.evidence import _EntailmentOutput
    from finch.content.checkers.portability import _PortabilityOutput
    from finch.content.checkers.safety import _SafetyOutput
    from finch.content.jobs import (
        AuthorPosition,
        ContentJob,
        ContentJobStatus,
        IntendedEffect,
        PlanTopicsOutput,
        SuccessCriterion,
        TopicProposal,
    )
    from finch.content.models import ClaimRef, Draft, DraftKind
    from finch.evidence.judge import BatchJudgeItem, BatchJudgeOutput
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent, JudgeScores
    from finch.graph.runtime import GraphRuntime
    from finch.settings import TwitterSettings
    from finch.twitter.models import Tweet

    class FakeReader:
        def __init__(self, gh, repo):
            self.repo = repo

        def sync(self, since=None):
            return []

    monkeypatch.setattr("finch.graph.daily.CommitReader", FakeReader)

    class FakeGh:
        def version(self):
            return "gh 1"

        def auth_status(self):
            return {"ok": True, "exit_code": 0, "detail": "ok"}

    class FakeOpen:
        def doctor(self):
            return {"ok": True, "exit_code": 0, "detail": "ok"}

        def version(self):
            return "opencli 1"

        def search(self, query, *, product="top", limit=20):
            return [
                Tweet(
                    id="t1",
                    author="u",
                    text="token bucket for the agent loop",
                    url="https://x.com/u/status/1",
                )
            ]

    class DummyExtractor:
        def extract(self, commits, repo):
            return [
                EngineeringEvent(
                    id="evt",
                    repository=repo,
                    commits=["abc123"],
                    problem=Claim(
                        statement="token bucket rate limiting",
                        confidence=ClaimConfidence.VERIFIED,
                    ),
                    decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
                    result=Claim(statement="tests pass", confidence=ClaimConfidence.VERIFIED),
                )
            ]

    class FakeRunner(CodexRunner):
        def __init__(self):
            self.calls = 0

        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model is BatchJudgeOutput:
                return BatchJudgeOutput(
                    items=[
                        BatchJudgeItem(
                            candidate_id="t1",
                            scores=JudgeScores(
                                relevance=0.9,
                                evidence_strength=0.9,
                                incremental_value=0.9,
                                discussability=0.9,
                            ),
                        )
                    ]
                )
            if output_model is PlanTopicsOutput:
                return PlanTopicsOutput(
                    items=[
                        TopicProposal(
                            id="tp1",
                            title="",
                            card_ids=["ev_evt_problem"],
                            candidate_id="t1",
                        )
                    ]
                )
            if output_model is ContentJob:
                return ContentJob(
                    id="job1",
                    source_card_ids=["ev_evt_problem"],
                    candidate_id="t1",
                    reader_problem="readers don't know how to rate limit",
                    audience="backend engineers",
                    intended_effect=IntendedEffect(
                        understand="token bucket rate limiting"
                    ),
                    author_position=AuthorPosition(
                        claim="use token bucket",
                        decision="use token bucket",
                        tradeoff="more memory",
                        confirmed=False,
                    ),
                    success_criteria=[
                        SuccessCriterion(
                            id="c1", description="critic passes", measurement="critic"
                        )
                    ],
                    recommended_format=DraftKind.REPLY,
                    status=ContentJobStatus.READY,
                )
            if output_model is _EntailmentOutput:
                return _EntailmentOutput(entailment_failed=[])
            if output_model is _DecisionOutput:
                return _DecisionOutput(
                    expresses_decision=True, expresses_tradeoff=True, missing=[]
                )
            if output_model is _PortabilityOutput:
                return _PortabilityOutput(generic_sentences=[])
            if output_model is _ActionabilityOutput:
                return _ActionabilityOutput(fulfills_effect=True, missing=[])
            if output_model is _SafetyOutput:
                return _SafetyOutput(
                    invented_personal_experience=False, unsupported_metric=False
                )
            if output_model is Draft:
                return Draft(
                    id="d1",
                    kind=DraftKind.REPLY,
                    candidate_id="t1",
                    language="en",
                    body="token bucket rate limiting is now in place",
                    claims=[
                        ClaimRef(
                            statement="token bucket rate limiting",
                            evidence_card_id="ev_evt_problem",
                            confidence=ClaimConfidence.VERIFIED,
                        )
                    ],
                )
            raise AssertionError(f"unexpected output_model: {output_model}")

    store = Store(tmp_path / "db.sqlite")
    store.init()
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        twitter=TwitterSettings(queries=[{"id": "q1", "text": "token bucket"}]),
    )
    runner = FakeRunner()

    def build():
        return daily_nodes(
            settings=settings,
            store=store,
            gh=FakeGh(),
            opencli=FakeOpen(),
            extractor=DummyExtractor(),
            runner=runner,
            commits_by_repo={"flingjie/FDE-Gym": []},
            known_commit_urls={"https://github.com/flingjie/FDE-Gym/commit/abc123"},
            repo_is_private={"flingjie/FDE-Gym": False},
        )

    run = GraphRuntime(store, build()).run()
    assert run.state == "NEEDS_INPUT"
    calls_after_first = runner.calls
    assert calls_after_first > 0

    # 模拟人工 confirm-position：把 repo 里的 job 置为 confirmed。
    jobs_repo = ContentJobRepository(store)
    job = jobs_repo.get_job("job1")
    assert job is not None and job.author_position is not None
    jobs_repo.upsert_job(
        job.model_copy(
            update={"author_position": job.author_position.model_copy(update={"confirmed": True})}
        )
    )

    run2 = GraphRuntime(store, build()).run(run_id=run.id)
    assert run2.state == "WAITING_FOR_REVIEW"
    # resume 新增 draft(1) + critique 检查器套件 LLM 调用：evidence entailment +
    # decision + portability + actionability + safety = 5 次。
    # specificity（无套话）、structure（无结构问题）、voice（空画像）走确定性路径，不调 LLM。
    assert runner.calls == calls_after_first + 6

    calls_after_second = runner.calls
    run3 = GraphRuntime(store, build()).run(run_id=run.id)
    assert run3.state == "WAITING_FOR_REVIEW"
    assert runner.calls == calls_after_second


def test_daily_no_evidence_completes_without_llm(tmp_path, monkeypatch):
    """Phase 0 回归：无证据卡时整条原创图到达 COMPLETED，不进入人工审核，也不调 LLM。"""
    import json

    from finch.graph.runtime import GraphRuntime
    from finch.settings import TwitterSettings

    class FakeReader:
        def __init__(self, gh, repo):
            self.repo = repo

        def sync(self, since=None):
            return []

    monkeypatch.setattr("finch.graph.daily.CommitReader", FakeReader)

    class FakeGh:
        def version(self):
            return "gh 1"

        def auth_status(self):
            return {"ok": True, "exit_code": 0, "detail": "ok"}

    class FakeOpen:
        def doctor(self):
            return {"ok": True, "exit_code": 0, "detail": "ok"}

        def version(self):
            return "opencli 1"

        def search(self, query, *, product="top", limit=20):
            return []

    class EmptyExtractor:
        def extract(self, commits, repo):
            return []

    class CountingRunner(CodexRunner):
        def __init__(self):
            self.calls = 0

        def run(self, prompt, output_model, **kw):
            self.calls += 1
            raise AssertionError(
                f"LLM runner must not be called in the no-evidence flow "
                f"(got {output_model.__name__})"
            )

    store = Store(tmp_path / "db.sqlite")
    store.init()
    settings = Settings(
        repositories=["flingjie/FDE-Gym"],
        twitter=TwitterSettings(queries=[{"id": "q1", "text": "token bucket"}]),
    )
    runner = CountingRunner()

    nodes = daily_nodes(
        settings=settings,
        store=store,
        gh=FakeGh(),
        opencli=FakeOpen(),
        extractor=EmptyExtractor(),
        runner=runner,
        commits_by_repo={"flingjie/FDE-Gym": []},
        known_commit_urls=set(),
        repo_is_private={"flingjie/FDE-Gym": False},
    )

    run = GraphRuntime(store, nodes).run()
    assert runner.calls == 0
    assert run.state == "COMPLETED"

    # 无 draft：draft 节点输出空 items，brief 判定 has_drafts=False。
    draft_rec = store.find_node(run.id, "draft", "default")
    assert draft_rec is not None
    assert json.loads(draft_rec.output_json)["items"] == []

    brief_rec = store.find_node(run.id, "brief", "default")
    assert brief_rec is not None
    assert json.loads(brief_rec.output_json)["terminal_state"] == "COMPLETED"
