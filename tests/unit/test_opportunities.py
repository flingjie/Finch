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
    OpportunityEvent,
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


# ---- Artifact 登记（add_artifact：成果登记事件）----

def _art_service(ws) -> OpportunityService:
    return OpportunityService(
        OpportunityRepository(ws), artifacts=ArtifactRepository(ws)
    )


def test_add_artifact_links_and_records_event(ws):
    service = _art_service(ws)
    service.create(opportunity_id="opp_abc", topic="t")
    service.select("opp_abc")
    art = Artifact(id="art_1", kind=ArtifactKind.METHOD_CARD)
    opp = service.add_artifact("opp_abc", art)

    assert "art_1" in opp.artifact_refs
    assert opp.revision == 3
    saved = ArtifactRepository(ws).get("opp_abc", "art_1")
    assert saved is not None
    assert saved.opportunity_id == "opp_abc"
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert events[-1].event_type == "artifact_added"


def test_add_artifact_is_idempotent(ws):
    service = _art_service(ws)
    service.create(opportunity_id="opp_abc", topic="t")
    service.add_artifact("opp_abc", Artifact(id="art_1", kind=ArtifactKind.DRAFT))
    opp = service.add_artifact("opp_abc", Artifact(id="art_1", kind=ArtifactKind.DRAFT))
    assert opp.artifact_refs == ["art_1"]
    assert opp.revision == 2  # 未重复递增


def test_add_artifact_conflict_raises(ws):
    service = _art_service(ws)
    service.create(opportunity_id="opp_abc", topic="t")
    with pytest.raises(OpportunityConflictError):
        service.add_artifact(
            "opp_abc", Artifact(id="art_1", kind=ArtifactKind.DRAFT), expected_revision=999
        )


# ---- request_id 幂等（§11.3：写命令带请求 ID，重复执行返回已有结果）----

def test_transition_request_id_is_idempotent(ws):
    service = OpportunityService(OpportunityRepository(ws))
    service.create(opportunity_id="opp_abc", topic="t")
    first = service.select("opp_abc", request_id="req_1")
    assert first.status == OpportunityStatus.SELECTED
    assert first.revision == 2

    # 重复执行同一 request_id → 返回当前快照，不重复应用、不报非法转换。
    second = service.select("opp_abc", request_id="req_1")
    assert second == first
    assert second.revision == 2
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events] == ["proposed", "selected"]


def test_distinct_request_ids_apply_distinct_transitions(ws):
    service = OpportunityService(OpportunityRepository(ws))
    service.create(opportunity_id="opp_abc", topic="t")
    service.select("opp_abc", request_id="req_select")
    opp = service.mark_ready("opp_abc", request_id="req_ready")
    assert opp.status == OpportunityStatus.READY
    assert opp.revision == 3
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.request_id for e in events] == [None, "req_select", "req_ready"]


def test_request_id_replays_stale_snapshot_after_crash(ws):
    """崩溃窗口：事件已落盘但快照未推进 → 重放同一 request_id 应修复快照而非静默返回旧快照。"""
    service = OpportunityService(OpportunityRepository(ws))
    service.create(opportunity_id="opp_abc", topic="t")
    # 模拟 append_event 与 save 之间崩溃：select 事件已落盘，快照仍停在 proposed(rev1)。
    service.repo.append_event(
        OpportunityEvent(
            event_id="opp_abc:r2",
            opportunity_id="opp_abc",
            event_type="selected",
            expected_revision=1,
            request_id="req_1",
        )
    )
    opp = service.select("opp_abc", request_id="req_1")
    assert opp.status == OpportunityStatus.SELECTED
    assert opp.revision == 2
    events = OpportunityRepository(ws).list_events("opp_abc")
    assert [e.event_type for e in events].count("selected") == 1  # 无重复事件


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


# ---- Reaction（用户对这条机会亲口说的话；append-only）----


def test_opportunity_reactions_default_empty_and_round_trip(ws):
    from finch.opportunities.models import Opportunity, Reaction

    repo = OpportunityRepository(ws)
    repo.save(Opportunity(id="opp_r", topic="t"))
    loaded = repo.get("opp_r")
    assert loaded is not None
    assert loaded.reactions == []

    repo.save(
        Opportunity(
            id="opp_r2",
            topic="t",
            reactions=[Reaction(seq=1, text="我当时最难的是不知道任务到底跑没跑")],
        )
    )
    loaded2 = repo.get("opp_r2")
    assert loaded2 is not None
    assert loaded2.reactions[0].seq == 1
    assert loaded2.reactions[0].text == "我当时最难的是不知道任务到底跑没跑"
    assert loaded2.reactions[0].created_at is not None


def test_legacy_opportunity_yaml_without_reactions_loads(ws):
    """旧快照没有 reactions 字段 → 默认空，行为与今天一致。"""
    from finch.opportunities.models import Opportunity

    path = ws.dir("opportunities") / "opp_old" / "opportunity.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "id: opp_old\nrevision: 1\ntopic: t\nstatus: proposed\n"
        "evidence_refs: []\nopen_questions: []\nartifact_refs: []\n",
        encoding="utf-8",
    )
    loaded = OpportunityRepository(ws).get("opp_old")
    assert loaded is not None
    assert isinstance(loaded, Opportunity)
    assert loaded.reactions == []
