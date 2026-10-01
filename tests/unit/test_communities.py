"""Unit tests for the community-scout domain (models/repository/service) + week_label."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from finch.communities.models import (
    CommunityCandidate,
    CommunityFeedback,
    CommunityProfile,
    CommunityResult,
    CommunityRun,
    EntryPoint,
    FeedbackFacts,
    RecommendationState,
    RunIntent,
    RunStep,
    ScoutAction,
    ScoutObservation,
    community_id_for,
    identity_key,
)
from finch.communities.service import CommunityNotFoundError, CommunityService, week_label
from finch.settings import Settings
from finch.storage.workspace import Workspace


def test_week_label_iso():
    assert week_label(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-W01"
    # 跨年周一（2025-12-29）仍属 2026-W01，不落在 2025-W53。
    assert week_label(datetime(2025, 12, 29, tzinfo=UTC)) == "2026-W01"
    assert week_label(datetime(2026, 12, 31, tzinfo=UTC)) == "2026-W53"
    assert week_label(datetime(2025, 1, 1, tzinfo=UTC)) == "2025-W01"


def test_community_id_is_content_addressed():
    a = community_id_for("Temporal Community")
    b = community_id_for("Temporal Community")
    c = community_id_for("Temporal Community ")
    d = community_id_for("Different Community")
    assert a == b == c
    assert a != d


def _profile(name: str = "Temporal Community") -> CommunityProfile:
    return CommunityProfile(name=name, platforms=["GitHub Discussions"], fit_score=86)


def test_save_derives_id_and_appends_candidate(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    assert saved.id.startswith("comm_")
    # 追加日志：同 name 再次保存 → 同 id，追加一条（不覆盖）。
    again = svc.save(_profile())
    assert again.id == saved.id
    assert len(svc.list_candidates()) == 2


def test_inspect_hit_and_miss(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    assert svc.inspect(saved.id) is not None
    assert svc.inspect("comm_missing") is None


def test_record_feedback_appends_and_latest(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    svc.record_feedback(saved.id, CommunityResult.JOINED, note="申请了")
    svc.record_feedback(saved.id, CommunityResult.INTERACTED)
    fb = svc.list_feedback()
    assert [f.result for f in fb] == [CommunityResult.JOINED, CommunityResult.INTERACTED]
    assert svc.repo.latest_feedback(saved.id).result == CommunityResult.INTERACTED


def test_snapshot_context_roundtrips(tmp_path):
    settings = Settings(paths={"var_dir": tmp_path})  # type: ignore[arg-type]
    settings.paths.var_dir = tmp_path
    ws = Workspace(tmp_path)
    ws.ensure()
    svc = CommunityService(ws)
    ctx = svc.snapshot_context(settings, ws)
    svc.repo.write_context(ctx)
    back = svc.repo.read_context()
    assert back is not None
    assert back.week == ctx.week
    assert back.interests == list(settings.interests.long_term_interests)


def test_old_profile_loads_with_defaults():
    data = {"id": "comm_x", "name": "Temporal Community", "fit_score": 80}
    p = CommunityProfile.model_validate(data)
    assert p.canonical_url == ""
    assert p.recommendation_state is None
    assert p.intent == "" and p.question == "" and p.practice_refs == []
    assert p.source_checked_at is None


def test_new_profile_fields_roundtrip():
    p = CommunityProfile(
        name="Temporal",
        canonical_url="https://temporal.io/community",
        recommendation_state=RecommendationState.ACTIONABLE,
        intent="question",
        question="failure replay",
        practice_refs=["FDE-Gym"],
        source_checked_at=datetime(2026, 9, 28, tzinfo=UTC),
        entry_point=EntryPoint(
            discussion="d", suggested_angle="a", url="https://x/1", status="open"
        ),
    )
    back = CommunityProfile.model_validate(p.model_dump(mode="json"))
    assert back.recommendation_state == RecommendationState.ACTIONABLE
    assert back.entry_point.url == "https://x/1"
    assert back.entry_point.status == "open"


def test_feedback_new_fields_default():
    fb = CommunityFeedback(community_id="comm_x", result=CommunityResult.INTERACTED)
    assert fb.reason_kind == "" and fb.interaction_ref == "" and fb.ref_kind == ""


def test_identity_key_uses_canonical_url_or_id():
    by_name = CommunityProfile(name="Temporal Community")
    assert identity_key(by_name) == community_id_for(by_name.name)
    with_url = CommunityProfile(
        name="Temporal", canonical_url="https://temporal.io/community"
    )
    assert identity_key(with_url) == "https://temporal.io/community"


def test_list_latest_profiles_dedups_by_id(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    svc.save(_profile())
    svc.save(_profile())  # 同 name → 同 id，追加第二条
    assert len(svc.repo.list_latest_profiles()) == 1


def test_list_latest_profiles_dedups_by_canonical_url_across_names(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    a = svc.save(
        CommunityProfile(
            name="Temporal Community", canonical_url="https://temporal.io/community"
        )
    )
    b = svc.save(
        CommunityProfile(name="Temporal", canonical_url="https://temporal.io/community")
    )
    assert a.id != b.id  # 不同 name → 不同 id
    latest = svc.repo.list_latest_profiles()
    assert len(latest) == 1  # 但同 URL 去重
    assert latest[0].name == "Temporal"  # 取最后追加的一条


def test_feedback_for_returns_history(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    svc.record_feedback(saved.id, CommunityResult.JOINED)
    svc.record_feedback(saved.id, CommunityResult.INTERACTED)
    assert len(svc.repo.feedback_for(saved.id)) == 2


def test_record_feedback_unknown_community_raises(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    with pytest.raises(CommunityNotFoundError):
        svc.record_feedback("comm_missing", CommunityResult.JOINED)


def test_record_feedback_stores_new_fields(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    saved = svc.save(_profile())
    fb = svc.record_feedback(
        saved.id,
        CommunityResult.INTERACTED,
        interaction_ref="https://x/1",
        ref_kind="public_url",
        reason_kind="deep_but_later",
    )
    assert fb.interaction_ref == "https://x/1"
    assert fb.ref_kind == "public_url"
    assert fb.reason_kind == "deep_but_later"


def test_latest_feedback_by_identity_crosses_name_drift(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    a = svc.save(
        CommunityProfile(name="Temporal Community", canonical_url="https://temporal.io/community")
    )
    svc.save(CommunityProfile(name="Temporal", canonical_url="https://temporal.io/community"))
    svc.record_feedback(a.id, CommunityResult.JOINED)
    by_identity = svc.repo.latest_feedback_by_identity()
    assert by_identity["https://temporal.io/community"].result == CommunityResult.JOINED


def test_list_latest_profiles_week_filters_before_dedup(tmp_path):
    svc = CommunityService(Workspace(tmp_path))
    svc.save(CommunityProfile(name="Temporal", week="2026-W01"))
    svc.save(CommunityProfile(name="Temporal", week="2026-W02"))
    assert [p.week for p in svc.repo.list_latest_profiles("2026-W01")] == ["2026-W01"]


def test_run_step_roundtrip():
    step = RunStep(
        run_id="r1",
        action=ScoutAction.SEARCH,
        observation=ScoutObservation(
            candidates=[CommunityCandidate(name="Temporal", canonical_url="https://temporal.io")]
        ),
        decision="kept 1 after excluding 0",
        outcome="candidates_found=1",
        at=datetime(2026, 9, 29, tzinfo=UTC),
        llm_calls=0,
    )
    back = RunStep.model_validate(step.model_dump(mode="json"))
    assert back.action == ScoutAction.SEARCH
    assert back.observation.candidates[0].name == "Temporal"
    assert back.llm_calls == 0


def test_community_run_roundtrip():
    run = CommunityRun(
        run_id="r1",
        intent=RunIntent.WEEKLY,
        goal="找 agent reliability 社区",
        week="2026-W40",
        status="done",
        candidates_found=5,
        cards_proposed=2,
        budget_used=5,
        started_at=datetime(2026, 9, 29, tzinfo=UTC),
        finished_at=datetime(2026, 9, 29, tzinfo=UTC),
    )
    assert CommunityRun.model_validate(run.model_dump(mode="json")) == run


def test_feedback_facts_defaults():
    f = FeedbackFacts()
    assert f.excluded == {}
    assert f.continue_framing == []
    assert f.summaries == {}


def test_runs_and_steps_roundtrip(tmp_path):
    from finch.communities.repository import CommunityRepository

    repo = CommunityRepository(Workspace(tmp_path))
    run = CommunityRun(
        run_id="r1", intent=RunIntent.WEEKLY, goal="g", week="2026-W40", status="done"
    )
    repo.append_run(run)
    assert repo.get_run("r1") == run
    assert [r.run_id for r in repo.list_runs()] == ["r1"]

    step = RunStep(run_id="r1", action=ScoutAction.SEARCH, decision="d")
    repo.append_step(step)
    repo.append_step(RunStep(run_id="r1", action=ScoutAction.FINISH, decision="f"))
    assert [s.action for s in repo.list_steps("r1")] == [
        ScoutAction.SEARCH,
        ScoutAction.FINISH,
    ]


def test_get_run_missing(tmp_path):
    from finch.communities.repository import CommunityRepository

    assert CommunityRepository(Workspace(tmp_path)).get_run("nope") is None


def test_snapshot_context_practice_refs_from_profile(tmp_path):
    profile = tmp_path / "practice-profile.yaml"
    profile.write_text(
        "items:\n"
        "  - id: a\n    domain: d\n    claim: c\n"
        "    evidence_refs: [https://github.com/flingjie/Agent-100-Days, https://x/1]\n"
        "    status: sourced\n    confirmed: true\n"
        "  - id: b\n    domain: d\n    claim: c\n"
        "    evidence_refs: [https://x/1]\n    status: sourced\n    confirmed: true\n"
        "  - id: c\n    domain: d\n    claim: c\n"
        "    evidence_refs: [https://ignored]\n    status: sourced\n    confirmed: false\n"
    )
    settings = Settings(
        paths={"var_dir": tmp_path, "practice_profile_path": profile},  # type: ignore[arg-type]
        interests={"practice_refs": ["from-config"]},  # type: ignore[arg-type]
    )
    ws = Workspace(tmp_path)
    ws.ensure()
    ctx = CommunityService(ws).snapshot_context(settings, ws)
    assert ctx.practice_refs == ["https://github.com/flingjie/Agent-100-Days", "https://x/1"]


def test_snapshot_context_practice_refs_fallback_to_config(tmp_path):
    settings = Settings(
        paths={"var_dir": tmp_path, "practice_profile_path": tmp_path / "missing.yaml"},  # type: ignore[arg-type]
        interests={"practice_refs": ["from-config"]},  # type: ignore[arg-type]
    )
    ws = Workspace(tmp_path)
    ws.ensure()
    ctx = CommunityService(ws).snapshot_context(settings, ws)
    assert ctx.practice_refs == ["from-config"]
