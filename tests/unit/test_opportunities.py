"""Tests for the new Opportunity aggregate (机会聚合 + 状态机 + 事件日志).

规范 §10.1/§11.1/§10.5：带生命周期状态机的交流机会聚合，快照 + append-only 事件日志，
乐观并发（expected_revision），替换旧的 engagement.Opportunity / InteractionProposal。
"""

import pytest

from finch.opportunities.models import (
    Artifact,
    ArtifactKind,
    ContributionForm,
    EntryKind,
    ExecutionStatus,
    MaterialOrigin,
    OpportunityStatus,
    Proposal,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository
from finch.opportunities.service import OpportunityConflictError, OpportunityService
from finch.storage.workspace import Workspace


@pytest.fixture()
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path)


@pytest.fixture()
def service(ws) -> OpportunityService:
    return OpportunityService(OpportunityRepository(ws))


def _proposal() -> Proposal:
    return Proposal(
        contribution="做一张 trace→最小回放 方法卡",
        form=ContributionForm.METHOD_CARD,
        expected_output="一张含输入/步骤/输出/限制的方法卡",
        scope="第一版只覆盖一个失败案例",
    )


# ---- 创建 ----

def test_create_saves_snapshot_and_event(ws, service):
    opp = service.create(
        opportunity_id="opp_abc",
        topic="失败回放",
        entry_kind=EntryKind.DIFFICULTY,
        why_me="与当前探索的回归测试直接相关",
        why_continue="作者已保存 trace，可补充边界",
        proposal=_proposal(),
    )
    assert opp.status == OpportunityStatus.PROPOSED
    assert opp.revision == 1

    repo = OpportunityRepository(ws)
    saved = repo.get("opp_abc")
    assert saved is not None
    assert saved.id == "opp_abc"
    assert saved.status == OpportunityStatus.PROPOSED
    assert saved.proposal == _proposal()

    events = repo.list_events("opp_abc")
    assert len(events) == 1
    assert events[0].event_type == "proposed"
    assert events[0].expected_revision == 0


# ---- 状态机 ----

def test_select_is_legal_from_proposed(service):
    service.create(opportunity_id="opp_abc", topic="t")
    opp = service.select("opp_abc", decision="accepted")
    assert opp.status == OpportunityStatus.SELECTED
    assert opp.revision == 2
    assert opp.decision == "accepted"


def test_mark_ready_requires_selected(service):
    service.create(opportunity_id="opp_abc", topic="t")
    with pytest.raises(ValueError, match="illegal transition"):
        service.mark_ready("opp_abc")
    service.select("opp_abc")
    opp = service.mark_ready("opp_abc")
    assert opp.status == OpportunityStatus.READY
    assert opp.revision == 3


def test_close_legal_from_proposed_and_ready_but_not_parked(service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.close("opp_abc")  # proposed -> closed 合法
    service.reopen("opp_abc")
    service.park("opp_abc")  # proposed -> parked 合法
    with pytest.raises(ValueError, match="illegal transition"):
        service.close("opp_abc")  # parked -> closed 非法


def test_reopen_from_parked_or_closed(service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.park("opp_abc")
    assert service.reopen("opp_abc").status == OpportunityStatus.PROPOSED
    service.select("opp_abc")
    service.mark_ready("opp_abc")
    service.close("opp_abc")
    assert service.reopen("opp_abc").status == OpportunityStatus.PROPOSED


def test_adjust_contribution_moves_ready_back_to_selected(service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.select("opp_abc")
    service.mark_ready("opp_abc")
    opp = service.select("opp_abc")  # ready -> selected（调整贡献）
    assert opp.status == OpportunityStatus.SELECTED


def test_transition_unknown_opportunity_raises(service):
    with pytest.raises(KeyError):
        service.select("opp_missing")


# ---- 乐观并发 ----

def test_expected_revision_conflict_raises(service):
    service.create(opportunity_id="opp_abc", topic="t")
    with pytest.raises(OpportunityConflictError):
        service.select("opp_abc", expected_revision=999)


def test_expected_revision_match_succeeds(service):
    service.create(opportunity_id="opp_abc", topic="t")
    opp = service.select("opp_abc", expected_revision=1)
    assert opp.revision == 2


# ---- 事件日志 ----

def test_transitions_append_events_with_expected_revision(ws, service):
    service.create(opportunity_id="opp_abc", topic="t")
    service.select("opp_abc")
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events] == ["proposed", "selected"]
    assert [e.expected_revision for e in events] == [0, 1]


# ---- Artifact（规范 §10.3：成果对象，material_origin / execution_status）----

def test_artifact_round_trip(ws):
    repo = ArtifactRepository(ws)
    art = Artifact(
        id="art_1",
        opportunity_id="opp_abc",
        kind=ArtifactKind.METHOD_CARD,
        material_origin=MaterialOrigin.REAL,
        execution_status=ExecutionStatus.RAN_OK,
        path="method_card.md",
    )
    repo.save(art)
    got = repo.get("opp_abc", "art_1")
    assert got == art


def test_artifact_defaults_are_synthetic_and_not_applicable():
    art = Artifact(id="a", opportunity_id="o", kind=ArtifactKind.DEMO)
    assert art.material_origin == MaterialOrigin.SYNTHETIC
    assert art.execution_status == ExecutionStatus.N_A


def test_artifact_list_by_opportunity(ws):
    repo = ArtifactRepository(ws)
    repo.save(Artifact(id="a1", opportunity_id="opp", kind=ArtifactKind.METHOD_CARD))
    repo.save(Artifact(id="a2", opportunity_id="opp", kind=ArtifactKind.DEMO))
    repo.save(Artifact(id="a3", opportunity_id="other", kind=ArtifactKind.DRAFT))
    assert [a.id for a in repo.list_by_opportunity("opp")] == ["a1", "a2"]


def test_execution_status_distinguishes_not_run_from_ran():
    # 规范 §6.4/§10.3：未运行方案不得标为已运行。
    not_run = Artifact(
        id="n", opportunity_id="o", kind=ArtifactKind.DEMO,
        execution_status=ExecutionStatus.NOT_RUN,
    )
    ran = Artifact(
        id="r", opportunity_id="o", kind=ArtifactKind.DEMO,
        execution_status=ExecutionStatus.RAN_OK,
    )
    assert not_run.execution_status != ExecutionStatus.RAN_OK
    assert ran.execution_status == ExecutionStatus.RAN_OK


def test_artifact_save_requires_opportunity_id(ws):
    repo = ArtifactRepository(ws)
    with pytest.raises(ValueError):
        repo.save(Artifact(id="orphan", kind=ArtifactKind.DRAFT))
