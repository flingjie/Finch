"""每日 Graph 与 Runtime 集成测试（Phase 4 Task D1）。"""

from finch.codex.runner import CodexRunner
from finch.evidence.extractor import Extractor
from finch.github.gh_client import GhClient
from finch.graph.daily import daily_nodes
from finch.settings import Settings
from finch.storage.database import Store
from finch.twitter.opencli_client import OpenCliClient


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
    ]
    assert nodes[4].reads == ["candidates", "evidence_cards"]
    assert nodes[5].writes == "match_results"
    assert nodes[5].reads == ["ranked_candidates", "evidence_cards", "candidates"]


def test_daily_runtime_full_pipeline_and_hydration(tmp_path, monkeypatch):
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
    assert run.state == "EVIDENCE_MATCHED"
    assert runner.calls == 1

    run2 = GraphRuntime(store, build()).run(run_id=run.id)
    assert run2.state == "EVIDENCE_MATCHED"
    assert runner.calls == 1
