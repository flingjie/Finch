"""每日 Graph 与 Runtime 集成测试（Phase 4 Task D1）。"""

from finch.codex.runner import CodexRunner
from finch.evidence.extractor import Extractor
from finch.github.gh_client import GhClient
from finch.graph.daily import daily_nodes
from finch.settings import Settings
from finch.storage.database import Store
from finch.twitter.opencli_client import OpenCliClient


def test_daily_nodes_has_nine_nodes(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    nodes = daily_nodes(
        settings=Settings(repositories=["flingjie/FDE-Gym"]),
        store=store,
        gh=GhClient(),
        opencli=OpenCliClient(),
        extractor=Extractor(CodexRunner()),
        runner=CodexRunner(),
        groups_by_repo={"flingjie/FDE-Gym": []},
        known_commit_urls=set(),
        repo_is_private={"flingjie/FDE-Gym": False},
    )
    assert [n.name for n in nodes] == [
        "preflight",
        "extract_events",
        "collect_tweets",
        "recall",
        "match_evidence",
        "select",
        "draft",
        "critique",
        "brief",
    ]
    assert nodes[5].reads == ["match_results", "evidence_cards", "candidates"]
    assert nodes[6].reads == ["ready_jobs", "evidence_cards", "candidates"]
    assert nodes[8].writes == "brief"
    assert nodes[8].terminal_state_key == "terminal_state"


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
        groups_by_repo={"flingjie/FDE-Gym": []},
        known_commit_urls=set(),
        repo_is_private={"flingjie/FDE-Gym": False},
    )
    assert [n.name for n in nodes] == [
        "preflight", "extract_events",
        "collect_tweets", "recall", "match_evidence",
        "select", "draft", "critique", "brief",
    ]
    assert nodes[3].reads == ["candidates", "evidence_cards"]
    assert nodes[4].writes == "match_results"
    assert nodes[4].reads == ["ranked_candidates", "evidence_cards", "candidates"]
    assert nodes[5].writes == "ready_jobs"
    assert nodes[7].reads == [
        "drafts", "match_results", "evidence_cards", "ready_jobs",
    ]


def test_daily_runtime_full_pipeline_and_hydration(tmp_path):
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
        def extract_grouped(self, groups, repo):
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
                        tradeoff="",
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
            groups_by_repo={"flingjie/FDE-Gym": []},
            known_commit_urls={"https://github.com/flingjie/FDE-Gym/commit/abc123"},
            repo_is_private={"flingjie/FDE-Gym": False},
        )

    run = GraphRuntime(store, build()).run()
    assert run.state == "WAITING_FOR_REVIEW"
    # 单轮直达（select 不阻塞）：match(1) + select(plan 1 + expand 1) + draft(1) +
    # critique(5) = 9 次 LLM 调用。critique 检查器套件 LLM 调用：evidence entailment +
    # decision + portability + actionability + safety = 5 次；specificity/structure/voice
    # 走确定性路径，不调 LLM。
    assert runner.calls == 9

    # resume 全绿：所有节点已成功，重放不新增 LLM 调用。
    run2 = GraphRuntime(store, build()).run(run_id=run.id)
    assert run2.state == "WAITING_FOR_REVIEW"
    assert runner.calls == 9


def test_daily_no_evidence_completes_without_llm(tmp_path):
    """Phase 0 回归：无证据卡时整条原创图到达 COMPLETED，不进入人工审核，也不调 LLM。"""
    import json

    from finch.graph.runtime import GraphRuntime
    from finch.settings import TwitterSettings

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
        def extract_grouped(self, groups, repo):
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
        groups_by_repo={"flingjie/FDE-Gym": []},
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

