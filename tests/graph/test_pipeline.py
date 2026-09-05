from finch.graph.pipeline import (
    make_collect_node,
    make_extract_node,
    make_preflight_node,
)
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store


def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    s.init()
    return s


class FakeGh:
    def __init__(self, version="gh 1", auth_ok=True, private=False):
        self._version = version
        self._auth_ok = auth_ok
        self._private = private
        self.synced = False
    def version(self): return self._version
    def auth_status(self): return {"ok": self._auth_ok, "exit_code": 0, "detail": "ok"}
    def repo_view(self, repo):
        from finch.github.models import RepoInfo
        return RepoInfo(name_with_owner=repo, default_branch="main",
                        url="https://github.com/"+repo, is_private=self._private)

class FakeOpen:
    def __init__(self, ok=True): self.ok = ok
    def doctor(self): return {"ok": self.ok, "exit_code": 0, "detail": "ok"}
    def version(self): return "opencli 1"
    def search(self, *a, **k): return []

def test_preflight_blocks_when_gh_missing(tmp_path):
    node = make_preflight_node(FakeGh(version=""), FakeOpen())
    run = GraphRuntime(_store(tmp_path), [node]).run()
    assert run.state == "BLOCKED"

def test_preflight_passes(tmp_path):
    node = make_preflight_node(FakeGh(), FakeOpen())
    run = GraphRuntime(_store(tmp_path), [node]).run()
    assert run.state == "PREFLIGHT_PASSED"

def test_extract_writes_cards_envelope(tmp_path, monkeypatch):
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
    from finch.storage.repositories import CommitIngestionRepository, EvidenceRepository
    store = _store(tmp_path)

    class DummyExtractor:
        def extract_grouped(self, groups, repo):
            return [EngineeringEvent(
                id="evt", repository=repo, commits=["abc123"],
                problem=Claim(
                    statement="false positive in eval",
                    confidence=ClaimConfidence.VERIFIED,
                ),
                decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="tests pass", confidence=ClaimConfidence.VERIFIED),
            )]

    # 工厂签名必须允许注入 extractor / groups_by_repo / known urls / repo_is_private / ledger
    node = make_extract_node(
        extractor=DummyExtractor(),
        groups_by_repo={"flingjie/FDE-Gym": []},  # 空 groups：DummyExtractor 仍返回 1 event
        repo_is_private={"flingjie/FDE-Gym": False},
        known_commit_urls={"https://github.com/flingjie/FDE-Gym/commit/abc123"},
        cards_repo=EvidenceRepository(store),
        ingestion_repo=CommitIngestionRepository(store),
        max_extract_retries=3,
    )
    run = GraphRuntime(store, [node]).run()
    assert run.state == "EVENTS_EXTRACTED"
    rec = store.find_node(run.id, "extract_events", "default")
    assert "items" in rec.output_json
    assert EvidenceRepository(store).get_card("ev_evt_problem") is not None


def test_collect_state(tmp_path):
    from datetime import UTC, datetime

    from finch.twitter.models import DiscussionCandidate

    def collect_fn() -> list:
        return [DiscussionCandidate(
            id="t1", author_handle="u", text="hello", url="https://x.com/u/status/1",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
        )]

    store = _store(tmp_path)
    run = GraphRuntime(store, [
        make_collect_node(collect_fn),
    ]).run()
    assert run.state == "TWEETS_COLLECTED"
    rec = store.find_node(run.id, "collect_tweets", "default")
    assert rec is not None
    assert "t1" in rec.output_json


def test_make_extract_node_parallel_preserves_card_order(tmp_path):
    from finch.evidence.models import Claim, ClaimConfidence, EngineeringEvent
    from finch.github.models import CommitDetail
    from finch.storage.repositories import CommitIngestionRepository, EvidenceRepository

    calls = []

    class FakeExtractor:
        def extract_grouped(self, groups, repo):
            calls.append(repo)
            return [EngineeringEvent(
                id=f"evt_{repo}", repository=repo, commits=[groups[0][0].sha],
                problem=Claim(statement="p", confidence=ClaimConfidence.SUPPORTED),
                decision=Claim(statement="d", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="r", confidence=ClaimConfidence.SUPPORTED),
            )]

    def _detail(sha):
        return CommitDetail(sha=sha, message="feat: x", author_date="2026-09-01T00:00:00Z",
                            html_url="u", parents=[], files=[])

    store = _store(tmp_path)
    sha_a = "a" * 40
    sha_b = "b" * 40
    groups_by_repo = {
        "a/x": [[_detail(sha_a)]],
        "b/y": [[_detail(sha_b)]],
    }
    node = make_extract_node(
        extractor=FakeExtractor(),
        groups_by_repo=groups_by_repo,
        repo_is_private={},
        known_commit_urls={
            f"https://github.com/a/x/commit/{sha_a}",
            f"https://github.com/b/y/commit/{sha_b}",
        },
        cards_repo=EvidenceRepository(store),
        ingestion_repo=CommitIngestionRepository(store),
        max_extract_retries=3,
    )
    result = node.run({})
    assert result.status == "succeeded"
    assert sorted(calls) == ["a/x", "b/y"]
    # 卡序 = repo 序：a/x 的卡在前。
    ids = [c["id"] for c in result.output["items"]]
    assert ids[0].startswith("ev_evt_a/x")
