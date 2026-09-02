from finch.graph.events import NodeResult
from finch.graph.pipeline import (
    make_collect_node, make_extract_node, make_preflight_node, make_sync_node,
)
from finch.graph.runtime import GraphRuntime
from finch.storage.database import Store

def _store(tmp_path):
    s = Store(tmp_path / "db.sqlite"); s.init(); return s

class FakeGh:
    def __init__(self, version="gh 1", auth_ok=True, private=False):
        self._version = version; self._auth_ok = auth_ok; self._private = private
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
    from finch.storage.repositories import EvidenceRepository
    store = _store(tmp_path)

    class DummyExtractor:
        def extract(self, commits, repo):
            return [EngineeringEvent(
                id="evt", repository=repo, commits=["abc123"],
                problem=Claim(statement="false positive in eval", confidence=ClaimConfidence.VERIFIED),
                decision=Claim(statement="add checks", confidence=ClaimConfidence.INFERRED),
                result=Claim(statement="tests pass", confidence=ClaimConfidence.VERIFIED),
            )]

    # 工厂签名必须允许注入 extractor / known urls / repo_is_private，见 Step 3
    node = make_extract_node(
        repo="flingjie/FDE-Gym",
        extractor=DummyExtractor(),
        commits=[],  # 空 commits：DummyExtractor 仍返回 1 event（测试用）
        repo_is_private={"flingjie/FDE-Gym": False},
        known_commit_urls={"https://github.com/flingjie/FDE-Gym/commit/abc123"},
        cards_repo=EvidenceRepository(store),
    )
    run = GraphRuntime(store, [node]).run()
    assert run.state == "EVENTS_EXTRACTED"
    rec = store.find_node(run.id, "extract_events", "default")
    assert "items" in rec.output_json
    assert EvidenceRepository(store).get_card("ev_evt_problem") is not None


def test_sync_and_collect_states(tmp_path):
    from datetime import UTC, datetime
    from finch.twitter.models import DiscussionCandidate

    flags = {"synced": False}

    def sync_fn() -> None:
        flags["synced"] = True

    def collect_fn() -> list:
        return [DiscussionCandidate(
            id="t1", author_handle="u", text="hello", url="https://x.com/u/status/1",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
        )]

    store = _store(tmp_path)
    run = GraphRuntime(store, [
        make_sync_node(sync_fn),
        make_collect_node(collect_fn),
    ]).run()
    assert flags["synced"] is True
    assert run.state == "TWEETS_COLLECTED"
    rec = store.find_node(run.id, "collect_tweets", "default")
    assert rec is not None
    assert "t1" in rec.output_json
