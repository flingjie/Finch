from datetime import UTC, datetime, timedelta

from finch.communities.models import (
    CommunityCandidate,
    CommunityFeedback,
    CommunityProfile,
    CommunityResult,
    RecommendationState,
    RunIntent,
    ScoutAction,
)
from finch.communities.repository import CommunityRepository
from finch.communities.scout import (
    CommunityLoop,
    WebFetcherSearchSource,
    candidate_identity,
    derive_feedback_facts,
)
from finch.settings import CommunityScoutSettings
from finch.storage.workspace import Workspace


def _fb(result: CommunityResult, *, days_ago: int = 0, reason: str = "", note: str = ""):
    return CommunityFeedback(
        community_id="comm_x",
        result=result,
        reason_kind=reason,
        note=note,
        at=datetime(2026, 9, 29, tzinfo=UTC) - timedelta(days=days_ago),
    )


def test_ignored_within_window_is_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.IGNORED, days_ago=7)}, now=now)
    assert "k" in facts.excluded
    assert facts.excluded["k"] == "ignored 1w ago"


def test_ignored_same_day_is_zero_weeks():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.IGNORED, days_ago=0)}, now=now)
    assert facts.excluded["k"] == "ignored 0w ago"


def test_ignored_beyond_window_not_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.IGNORED, days_ago=35)}, now=now
    )
    assert "k" not in facts.excluded


def test_no_time_never_excluded():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=1, reason="no_time")}, now=now
    )
    assert "k" not in facts.excluded


def test_engaged_results_enter_continue_framing():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts({"k": _fb(CommunityResult.INTERACTED, days_ago=2)}, now=now)
    assert facts.continue_framing == ["k"]


def test_summaries_carry_reason_and_note():
    now = datetime(2026, 9, 29, tzinfo=UTC)
    facts = derive_feedback_facts(
        {"k": _fb(CommunityResult.SAVED, days_ago=3, reason="deep_but_later", note="先观察")},
        now=now,
    )
    assert "deep_but_later" in facts.summaries["k"]
    assert "先观察" in facts.summaries["k"]


def test_candidate_identity_prefers_canonical_url():
    assert (
        candidate_identity(CommunityCandidate(name="Temporal", canonical_url="https://t.io"))
        == "https://t.io"
    )


def test_candidate_identity_falls_back_to_name_hash():
    c = CommunityCandidate(name="Temporal")
    assert candidate_identity(c) == candidate_identity(CommunityCandidate(name="Temporal"))


class FakeRunner:
    def __init__(self):
        self.calls = 0
        self.last_model = None

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_model = output_model
        return _fake_output(output_model)


def _fake_output(model):
    # 按 output_model 类型回传对应结构（inspect → 一个 verified 候选；propose → 一张卡）。
    if model.__name__ == "InspectOutput":
        from finch.communities.scout import InspectedCandidate, InspectOutput

        return InspectOutput(
            candidates=[
                InspectedCandidate(
                    name="Temporal",
                    canonical_url="https://temporal.io/community",
                    recommendation_state=RecommendationState.ACTIONABLE,
                    why_fit=["durable execution"],
                    evidence_urls=["https://temporal.io/community"],
                )
            ]
        )
    from finch.communities.scout import ProposeOutput

    return ProposeOutput(
        cards=[
            CommunityProfile(
                name="Temporal",
                canonical_url="https://temporal.io/community",
                recommendation_state=RecommendationState.ACTIONABLE,
                why_fit=["durable execution"],
                evidence_urls=["https://temporal.io/community"],
            )
        ]
    )


class FakeSearchSource:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = 0

    def search(self, intent, goal, limit):
        self.calls += 1
        return self.candidates


def _loop(tmp_path, candidates, **budget_overrides):
    repo = CommunityRepository(Workspace(tmp_path))
    budget = CommunityScoutSettings(**budget_overrides)
    return (
        CommunityLoop(FakeRunner(), FakeSearchSource(candidates), repo, budget=budget),
        repo,
    )


def test_loop_runs_fixed_action_order(tmp_path):
    loop, repo = _loop(
        tmp_path,
        [CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community")],
    )
    run = loop.run(RunIntent.WEEKLY, "找社区")
    actions = [s.action for s in repo.list_steps(run.run_id)]
    assert actions == [
        ScoutAction.SEARCH,
        ScoutAction.INSPECT,
        ScoutAction.PROPOSE,
        ScoutAction.FINISH,
    ]
    assert run.status == "done"
    assert run.cards_proposed == 1


def test_loop_excludes_ignored_candidate(tmp_path):
    from finch.communities.models import CommunityFeedback, CommunityResult

    loop, repo = _loop(
        tmp_path,
        [
            CommunityCandidate(name="Ignored Community", canonical_url="https://ig.io"),
            CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community"),
        ],
    )
    # 预置一条「ignored」反馈，使 https://ig.io 被硬门禁排除。
    repo.append_feedback(
        CommunityFeedback(community_id="https://ig.io", result=CommunityResult.IGNORED)
    )
    run = loop.run(RunIntent.WEEKLY, "找社区")
    search_step = repo.list_steps(run.run_id)[0]
    names = [c.name for c in search_step.observation.candidates]
    assert "Ignored Community" not in names
    assert "Temporal" in names


def test_loop_reinspects_once_when_all_rejected(tmp_path):
    from finch.communities.scout import InspectedCandidate, InspectOutput

    class RejectThenVerifyRunner(FakeRunner):
        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model.__name__ == "InspectOutput" and self.calls == 1:
                return InspectOutput(
                    candidates=[InspectedCandidate(name="A", reject_reason="无公开证据")]
                )
            return super().run(prompt, output_model, **kw)

    loop, repo = _loop(
        tmp_path,
        [
            CommunityCandidate(name="A"),
            CommunityCandidate(name="B"),
            CommunityCandidate(name="C"),
        ],
        inspect_batch=1,
    )
    loop.runner = RejectThenVerifyRunner()
    run = loop.run(RunIntent.WEEKLY, "找社区")
    inspects = [s for s in repo.list_steps(run.run_id) if s.action == ScoutAction.INSPECT]
    assert len(inspects) == 2  # 本批全淘汰 → 再 inspect 下一批一次


def test_observe_candidate_does_not_reinspect(tmp_path):
    from finch.communities.scout import InspectedCandidate, InspectOutput

    class ObserveRunner(FakeRunner):
        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model.__name__ == "InspectOutput":
                return InspectOutput(
                    candidates=[
                        InspectedCandidate(
                            name="A",
                            recommendation_state=RecommendationState.OBSERVE,
                        )
                    ]
                )
            return super().run(prompt, output_model, **kw)

    loop, repo = _loop(
        tmp_path,
        [CommunityCandidate(name="A"), CommunityCandidate(name="B")],
        inspect_batch=1,
    )
    loop.runner = ObserveRunner()
    run = loop.run(RunIntent.WEEKLY, "找社区")
    inspects = [s for s in repo.list_steps(run.run_id) if s.action == ScoutAction.INSPECT]
    assert len(inspects) == 1  # observe（非 actionable）也算「有产出」→ 不 re-inspect


def test_inspect_candidate_not_in_batch_is_rejected(tmp_path):
    from finch.communities.scout import InspectedCandidate, InspectOutput

    class HallucinatedInspectRunner(FakeRunner):
        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model.__name__ == "InspectOutput":
                return InspectOutput(
                    candidates=[
                        InspectedCandidate(
                            name="Hallucinated",
                            canonical_url="https://fake.io",
                            recommendation_state=RecommendationState.ACTIONABLE,
                        )
                    ]
                )
            return super().run(prompt, output_model, **kw)

    loop, repo = _loop(
        tmp_path,
        [CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community")],
    )
    loop.runner = HallucinatedInspectRunner()
    run = loop.run(RunIntent.WEEKLY, "找社区")
    inspect_step = [s for s in repo.list_steps(run.run_id) if s.action == ScoutAction.INSPECT][0]
    assert inspect_step.observation.verified == []
    assert inspect_step.observation.rejected == [
        {"name": "Hallucinated", "reason": "未匹配输入候选（丢弃）"}
    ]
    assert run.cards_proposed == 0


def test_propose_card_not_in_verified_is_dropped(tmp_path):
    from finch.communities.scout import ProposeOutput

    class HallucinatedProposeRunner(FakeRunner):
        def run(self, prompt, output_model, **kw):
            self.calls += 1
            if output_model.__name__ == "ProposeOutput":
                return ProposeOutput(
                    cards=[
                        CommunityProfile(
                            name="Hallucinated", canonical_url="https://fake.io"
                        )
                    ]
                )
            return super().run(prompt, output_model, **kw)

    loop, repo = _loop(
        tmp_path,
        [CommunityCandidate(name="Temporal", canonical_url="https://temporal.io/community")],
    )
    loop.runner = HallucinatedProposeRunner()
    run = loop.run(RunIntent.WEEKLY, "找社区")
    propose_step = [s for s in repo.list_steps(run.run_id) if s.action == ScoutAction.PROPOSE][0]
    assert propose_step.observation.cards == []
    assert run.cards_proposed == 0
    assert repo.list_candidates() == []


class _FakeFetcher:
    def fetch(self, url):
        return f"fetched: {url}"


def test_web_fetcher_source_yields_candidates():
    src = WebFetcherSearchSource(
        ["https://temporal.io/community", "https://example.org/forum"],
        fetcher=_FakeFetcher(),
    )
    out = src.search(RunIntent.WEEKLY, "找社区", limit=10)
    assert [c.canonical_url for c in out] == [
        "https://temporal.io/community",
        "https://example.org/forum",
    ]
    assert out[0].evidence_text == "fetched: https://temporal.io/community"
