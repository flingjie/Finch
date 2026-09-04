"""互动双轨端到端测试（Phase 7，无真实网络/LLM）。

通过 ``run_dual_track`` + 真实互动流程（``run_discovery_engagement_flow``，fakes 驱动）+
持久化（``_persist_engagement_candidates`` / ``_persist_engagement_run_stats``，即
``run_daily`` 的同款路径）覆盖执行计划 §7 端到端场景 1 / 3 / 4 / 5 / 6 / 7。
"""

from finch.cli import _persist_engagement_candidates, _persist_engagement_run_stats
from finch.codex.runner import CodexRunner
from finch.content.models import Draft, DraftKind
from finch.engagement.flow import run_discovery_engagement_flow
from finch.engagement.guard import ExecutionStatus, evaluate_execution
from finch.engagement.models import InteractionAction, InteractionStatus
from finch.engagement.proposals import ProposalBatchOutput, ProposalItem
from finch.engagement.scoring import ConversationScoreInput, ScoreBatchOutput, ScoreItem
from finch.graph.dual_track import run_dual_track
from finch.settings import EngagementSettings, InterestsSettings, Settings
from finch.storage.database import RunRecord, Store
from finch.storage.repositories import (
    DraftRepository,
    EngagementRunStatsRepository,
    InteractionRepository,
)
from finch.twitter.models import Tweet


class FakeOpenCliClient:
    """返回固定 tweets 的 opencli 替身（X 搜索只读）。"""

    def __init__(self, tweets: list[Tweet] | None = None):
        self._tweets = tweets or []

    def search(self, query, *, product="top", limit=20):
        return list(self._tweets)


class FakeRunner(CodexRunner):
    """输出模型感知的 CodexRunner 替身：评分 → ScoreBatchOutput，提案 → ProposalBatchOutput。"""

    def __init__(
        self,
        scores: list[ScoreItem] | None = None,
        proposals: list[ProposalItem] | None = None,
    ):
        self.scores = scores or []
        self.proposals = proposals or []

    def run(self, prompt, output_model, **kwargs):
        if output_model is ScoreBatchOutput:
            return ScoreBatchOutput(items=self.scores)
        if output_model is ProposalBatchOutput:
            return ProposalBatchOutput(items=self.proposals)
        raise AssertionError(f"unexpected output_model: {output_model}")


def _tweet(tid: str = "p1", author: str = "alice") -> Tweet:
    return Tweet(
        id=tid,
        author=author,
        text="How do you test agent reliability in production systems?",
        created_at="Wed Sep 03 12:00:00 +0000 2025",
        likes=5,
        views=50,
        url=f"https://x.com/{author}/status/{tid}",
    )


def _settings() -> Settings:
    return Settings(
        engagement=EngagementSettings(platforms=["x"], max_posts_scanned=30),
        interests=InterestsSettings(stable=["agent reliability"]),
    )


def _score_item(post_id: str, **overrides) -> ScoreItem:
    dims = dict(
        relevance=0.9,
        novelty=0.9,
        discussability=0.9,
        practical_evidence=0.9,
        relationship_value=0.9,
        reasons=["on topic", "debatable"],
    )
    dims.update(overrides)
    return ScoreItem(post_id=post_id, scores=ConversationScoreInput(**dims))


def _proposal(post_id: str) -> ProposalItem:
    return ProposalItem(
        post_id=post_id,
        draft="Have you tried recording a failure replay and diffing it?",
        intent="propose a verification method",
        source_summary="the claim about prod reliability",
        factual_risks=[],
    )


def _original_empty(store: Store, state: str = "COMPLETED"):
    """原创轨道成功但无草稿（无成熟证据 → 空结果）。"""

    def _run(rid: str) -> RunRecord:
        record = RunRecord(id=rid, state=state)
        store.upsert_run(record)
        return record

    return _run


def _original_draft(store: Store):
    """原创轨道产出并持久化一条原创草稿。"""

    def _run(rid: str) -> RunRecord:
        record = RunRecord(id=rid, state="COMPLETED")
        store.upsert_run(record)
        DraftRepository(store).upsert_draft(
            Draft(id=f"draft-{rid}", kind=DraftKind.ORIGINAL, body="an original note")
        )
        return record

    return _run


def _raise(exc: Exception):
    def _run(rid: str) -> RunRecord:
        raise exc

    return _run


def _engagement_track(settings, opencli, runner):
    return lambda rid: run_discovery_engagement_flow(settings, opencli, runner, run_id=rid)


def test_both_tracks_produce_candidates(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    opencli = FakeOpenCliClient(tweets=[_tweet("p1")])
    runner = FakeRunner(scores=[_score_item("p1")], proposals=[_proposal("p1")])
    settings = _settings()

    result = run_dual_track(
        original_track=_original_draft(store),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)
    _persist_engagement_run_stats(result, store, latency_ms=123)

    assert result.partial_failure is False
    assert result.original is not None and result.original.state == "COMPLETED"
    assert result.engagement is not None and result.engagement.status == "succeeded"
    # 原创草稿已持久化。
    assert len(DraftRepository(store).list_drafts()) == 1
    # 互动候选已持久化，且携带草稿。
    candidates = InteractionRepository(store).list_all()
    assert len(candidates) == 1
    assert candidates[0].action in (InteractionAction.DRAFT_REPLY, InteractionAction.DRAFT_QUOTE)
    assert candidates[0].draft == "Have you tried recording a failure replay and diffing it?"


def test_no_original_but_engagement_candidate(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    opencli = FakeOpenCliClient(tweets=[_tweet("p1")])
    runner = FakeRunner(scores=[_score_item("p1")], proposals=[_proposal("p1")])
    settings = _settings()

    result = run_dual_track(
        original_track=_original_empty(store),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)

    # 原创轨道成功但无草稿（无证据），互动轨道仍产出候选。
    assert result.original is not None and result.original.state == "COMPLETED"
    assert len(DraftRepository(store).list_drafts()) == 0
    assert result.engagement is not None and result.engagement.status == "succeeded"
    assert len(InteractionRepository(store).list_all()) == 1


def test_original_preserved_engagement_empty(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    # 帖子存在但评分低于阈值 → 搜索到帖子、无合格候选。
    opencli = FakeOpenCliClient(tweets=[_tweet("p1")])
    runner = FakeRunner(
        scores=[
            _score_item(
                "p1",
                relevance=0.1,
                novelty=0.1,
                discussability=0.1,
                practical_evidence=0.1,
                relationship_value=0.1,
            )
        ],
        proposals=[],
    )
    settings = _settings()

    result = run_dual_track(
        original_track=_original_draft(store),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)

    # 原创草稿保留，互动轨道无候选。
    assert result.original is not None and result.original.state == "COMPLETED"
    assert len(DraftRepository(store).list_drafts()) == 1
    assert result.engagement is not None and result.engagement.status == "succeeded"
    assert result.engagement.candidates == []
    assert InteractionRepository(store).list_all() == []


def test_both_empty_success_no_forced_content(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    opencli = FakeOpenCliClient(tweets=[])
    runner = FakeRunner()
    settings = _settings()

    result = run_dual_track(
        original_track=_original_empty(store),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)
    _persist_engagement_run_stats(result, store, latency_ms=0)

    assert result.partial_failure is False
    assert result.status == "succeeded"
    assert result.engagement is not None and result.engagement.status == "empty"
    assert result.engagement.candidates == []
    assert InteractionRepository(store).list_all() == []
    assert len(DraftRepository(store).list_drafts()) == 0
    # 空运行也写入运行统计，供 no_evidence_runs 计数。
    stats = EngagementRunStatsRepository(store).list_all()
    assert len(stats) == 1
    assert stats[0].posts_scanned == 0


def test_one_track_raises_other_preserved_partial_failure(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    opencli = FakeOpenCliClient(tweets=[_tweet("p1")])
    runner = FakeRunner(scores=[_score_item("p1")], proposals=[_proposal("p1")])
    settings = _settings()

    result = run_dual_track(
        original_track=_raise(RuntimeError("boom")),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)

    assert result.partial_failure is True
    assert result.status == "partial_failure"
    assert result.original is None
    assert result.original_error is not None
    assert result.engagement is not None and result.engagement.status == "succeeded"
    assert len(InteractionRepository(store).list_all()) == 1


def test_rejected_candidate_never_executable(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.init()
    opencli = FakeOpenCliClient(tweets=[_tweet("p1")])
    runner = FakeRunner(scores=[_score_item("p1")], proposals=[_proposal("p1")])
    settings = _settings()

    result = run_dual_track(
        original_track=_original_empty(store),
        engagement_track=_engagement_track(settings, opencli, runner),
    )
    _persist_engagement_candidates(result, store)

    repo = InteractionRepository(store)
    candidate = repo.list_all()[0]
    repo.reject(candidate.id, "fact_error")

    rejected = repo.get(candidate.id)
    assert rejected.status is InteractionStatus.REJECTED
    assert rejected.reject_reason == "fact_error"

    # 拒绝后的候选即使其余前置条件全通过，执行守卫也判定为 REJECTED（不可执行）。
    status, reasons = evaluate_execution(
        rejected,
        post_available=True,
        draft_unchanged=True,
        author_today_count=0,
        public_today_count=0,
        engagement=EngagementSettings(),
    )
    assert status is ExecutionStatus.REJECTED
    assert reasons == ["not approved"]
