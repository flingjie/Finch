"""StyleObservationService：聚合触发、确认状态、不写画像、证据来源。"""

from datetime import UTC, datetime

from finch.practice.models import PracticeOption, PracticeSession
from finch.practice.observations import StyleObservationService
from finch.storage.repositories import PracticeSessionRepository, StyleObservationRepository
from finch.storage.workspace import Workspace


def _session(uid, *, dimension, optname, final, final_source="user_authored", status="finished"):
    return PracticeSession(
        id=f"practice_{uid}",
        initial_attempt="用户首稿",
        practice_dimension=dimension,
        options=[
            PracticeOption(name=optname, familiarity="熟悉", entry_point="e", dimension=dimension),
            PracticeOption(name="other", familiarity="陌生", entry_point="e2", dimension="节奏"),
        ],
        selected_option=0,
        final_expression=final,
        final_source=final_source,
        status=status,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _service(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    return ws, StyleObservationService(
        PracticeSessionRepository(ws), StyleObservationRepository(ws)
    )


def _upsert(ws, *sessions):
    for s in sessions:
        PracticeSessionRepository(ws).upsert(s)


def test_propose_requires_three_similar_sessions(tmp_path):
    ws, svc = _service(tmp_path)
    _upsert(
        ws,
        _session("a1", dimension="结构", optname="A", final="句1"),
        _session("a2", dimension="结构", optname="A", final="句2"),
        _session("b1", dimension="节奏", optname="B", final="句3"),
        _session("b2", dimension="节奏", optname="B", final="句4"),
    )
    proposed = svc.propose()
    assert proposed == []  # 都不足 3 次

    _upsert(ws, _session("a3", dimension="结构", optname="A", final="句5"))
    proposed = svc.propose()
    assert len(proposed) == 1
    obs = proposed[0]
    assert obs.status == "pending"
    assert "结构" in obs.characteristic and "A" in obs.characteristic
    assert len(obs.evidence) == 3


def test_propose_is_idempotent_and_rejected_persists(tmp_path):
    ws, svc = _service(tmp_path)
    _upsert(
        ws,
        _session("a1", dimension="结构", optname="A", final="句1"),
        _session("a2", dimension="结构", optname="A", final="句2"),
        _session("a3", dimension="结构", optname="A", final="句3"),
    )
    obs_id = svc.propose()[0].id
    assert svc.propose() == []  # 已存在，不重复提出

    svc.reject(obs_id)
    assert svc.list_all()[0].status == "rejected"
    assert svc.propose() == []  # rejected 保留，不反复提出


def test_accept_and_correct(tmp_path):
    ws, svc = _service(tmp_path)
    _upsert(
        ws,
        _session("a1", dimension="结构", optname="A", final="句1"),
        _session("a2", dimension="结构", optname="A", final="句2"),
        _session("a3", dimension="结构", optname="A", final="句3"),
    )
    obs_id = svc.propose()[0].id
    svc.accept(obs_id)
    assert svc.list_all()[0].status == "accepted"

    obs_id2 = svc.propose(force=True)[0].id
    svc.correct(obs_id2, "其实是「先给结论」而不是「结构」")
    corrected = StyleObservationRepository(ws).get(obs_id2)
    assert corrected.status == "corrected"
    assert "先给结论" in corrected.counterexamples


def test_mixed_source_uses_initial_attempt_as_evidence(tmp_path):
    ws, svc = _service(tmp_path)
    _upsert(
        ws,
        _session("m1", dimension="语气", optname="C", final="AI 写的终稿", final_source="mixed"),
        _session("m2", dimension="语气", optname="C", final="AI 写的终稿2", final_source="mixed"),
        _session("m3", dimension="语气", optname="C", final="AI 写的终稿3", final_source="mixed"),
    )
    proposed = svc.propose()
    assert len(proposed) == 1
    # 混合来源：证据退回首稿（必为用户创作），不用 AI 终稿
    assert all(e.quote == "用户首稿" for e in proposed[0].evidence)


def test_observation_repository_missing_raises(tmp_path):
    ws, svc = _service(tmp_path)
    import pytest

    with pytest.raises(KeyError):
        svc.accept("sobs_none")
