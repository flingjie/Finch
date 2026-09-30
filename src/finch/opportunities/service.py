"""机会聚合服务：创建 + 状态机转换 + 乐观并发（规范 §11.1/§11.3）。

状态转换表来自规范 §11.1 状态图；每次转换先追加事件（带 ``expected_revision`` 锚点）
再更新快照，``revision`` 递增。乐观并发：调用方提供 ``expected_revision``，与快照
不一致即抛 ``OpportunityConflictError``（显式冲突）。

事实来源：``opportunity.yaml`` 快照是唯一事实来源；``events.jsonl`` 只作审计，
不用于重建状态（MVP 不做事件重放）。读改写由仓库级写锁串行化，``expected_revision``
仅作跨进程乐观校验的补充。
"""

from datetime import UTC, datetime

from finch.opportunities.models import (
    Artifact,
    EntryKind,
    EvidenceRef,
    ExecutionStatus,
    MaterialOrigin,
    Opportunity,
    OpportunityEvent,
    OpportunityStatus,
    Proposal,
)
from finch.opportunities.repository import ArtifactRepository, OpportunityRepository


class OpportunityConflictError(Exception):
    """乐观并发冲突：快照 revision 与调用方 expected_revision 不一致。"""


# 规范 §11.1 状态图：源状态 → 合法目标状态集合。
_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    OpportunityStatus.PROPOSED: {
        OpportunityStatus.SELECTED,
        OpportunityStatus.PARKED,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.SELECTED: {
        OpportunityStatus.READY,
        OpportunityStatus.PARKED,
    },
    OpportunityStatus.READY: {
        OpportunityStatus.SELECTED,
        OpportunityStatus.CLOSED,
    },
    OpportunityStatus.PARKED: {OpportunityStatus.PROPOSED},
    OpportunityStatus.CLOSED: {OpportunityStatus.PROPOSED},
}


class OpportunityService:
    """机会聚合领域服务：只依赖注入的 ``OpportunityRepository``（+ 可选 ArtifactRepository），
    不调 LLM、不自动重试。"""

    def __init__(
        self,
        repo: OpportunityRepository,
        artifacts: ArtifactRepository | None = None,
    ) -> None:
        self.repo = repo
        self.artifacts = artifacts

    def locked(self, opportunity_id: str):
        """对该机会的读改写加写锁（可重入），供外部组合操作使用。"""
        return self.repo.locked(opportunity_id)

    def create(
        self,
        *,
        opportunity_id: str,
        person_ref: str | None = None,
        thread_ref: str | None = None,
        topic: str = "",
        entry_kind: EntryKind | None = None,
        why_me: str = "",
        why_continue: str = "",
        proposal: Proposal | None = None,
        evidence_refs: list[EvidenceRef] | None = None,
        open_questions: list[str] | None = None,
    ) -> Opportunity:
        """创建 PROPOSED 机会（revision=1），写 proposed 事件 + 快照。"""
        opp = Opportunity(
            id=opportunity_id,
            person_ref=person_ref,
            thread_ref=thread_ref,
            topic=topic,
            entry_kind=entry_kind,
            why_me=why_me,
            why_continue=why_continue,
            proposal=proposal,
            evidence_refs=evidence_refs or [],
            open_questions=open_questions or [],
        )
        return self.create_from(opp)

    def create_from(self, opportunity: Opportunity) -> Opportunity:
        """用预构建的 Opportunity 落库（写 proposed 事件 + 快照）。"""
        with self.repo.locked(opportunity.id):
            self.repo.append_event(
                OpportunityEvent(
                    event_id=f"{opportunity.id}:r1",
                    opportunity_id=opportunity.id,
                    event_type="proposed",
                    expected_revision=0,
                )
            )
            self.repo.save(opportunity)
            return opportunity

    def get(self, opportunity_id: str) -> Opportunity | None:
        """读快照；不存在返回 None。"""
        return self.repo.get(opportunity_id)

    def add_artifact(
        self,
        opportunity_id: str,
        artifact: Artifact,
        *,
        expected_revision: int | None = None,
    ) -> Opportunity:
        """登记成果（§10.4「任务完成」）：保存 Artifact 并链到机会，revision 递增。

        幂等：同一 artifact id 已登记则原样返回。需要注入 ``ArtifactRepository``。
        """
        if self.artifacts is None:
            raise ValueError("add_artifact requires an ArtifactRepository")
        with self.repo.locked(opportunity_id):
            opp = self.repo.get(opportunity_id)
            if opp is None:
                raise KeyError(opportunity_id)
            if expected_revision is not None and expected_revision != opp.revision:
                raise OpportunityConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {opp.revision}"
                )
            if artifact.id in opp.artifact_refs:
                return opp
            self.artifacts.save(
                artifact.model_copy(update={"opportunity_id": opportunity_id})
            )
            new_opp = opp.model_copy(
                update={
                    "artifact_refs": [*opp.artifact_refs, artifact.id],
                    "revision": opp.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repo.append_event(
                OpportunityEvent(
                    event_id=f"{opportunity_id}:r{new_opp.revision}",
                    opportunity_id=opportunity_id,
                    event_type="artifact_added",
                    expected_revision=opp.revision,
                )
            )
            self.repo.save(new_opp)
            return new_opp

    def update_artifact(
        self,
        opportunity_id: str,
        artifact_id: str,
        *,
        execution_status: ExecutionStatus | None = None,
        material_origin: MaterialOrigin | None = None,
        author_note: str | None = None,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> Artifact:
        """回填演示/成果事实（§6.4）：仅用户声明可将 not_run 改为 ran_*。

        代码不得自行把未运行标为已运行；本方法是唯一合法写入路径。
        """
        if self.artifacts is None:
            raise ValueError("update_artifact requires an ArtifactRepository")
        with self.repo.locked(opportunity_id):
            opp = self.repo.get(opportunity_id)
            if opp is None:
                raise KeyError(opportunity_id)
            if expected_revision is not None and expected_revision != opp.revision:
                raise OpportunityConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {opp.revision}"
                )
            if request_id is not None:
                for e in self.repo.list_events(opportunity_id):
                    if e.request_id == request_id and e.event_type == "artifact_updated":
                        art = self.artifacts.get(opportunity_id, artifact_id)
                        if art is None:
                            raise KeyError(artifact_id)
                        return art
            art = self.artifacts.get(opportunity_id, artifact_id)
            if art is None:
                raise KeyError(artifact_id)
            updates: dict = {}
            if execution_status is not None:
                updates["execution_status"] = execution_status
            if material_origin is not None:
                updates["material_origin"] = material_origin
            if author_note is not None:
                updates["author_note"] = author_note
            if not updates:
                return art
            updates["revision"] = art.revision + 1
            new_art = art.model_copy(update=updates)
            self.artifacts.save(new_art)
            new_opp = opp.model_copy(
                update={
                    "revision": opp.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repo.append_event(
                OpportunityEvent(
                    event_id=f"{opportunity_id}:r{new_opp.revision}",
                    opportunity_id=opportunity_id,
                    event_type="artifact_updated",
                    expected_revision=opp.revision,
                    request_id=request_id,
                )
            )
            self.repo.save(new_opp)
            return new_art

    def select(
        self,
        opportunity_id: str,
        *,
        expected_revision: int | None = None,
        decision: str | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """用户选定方向（proposed → selected；ready → selected 表示调整贡献）。"""
        return self._transition(
            opportunity_id,
            OpportunityStatus.SELECTED,
            expected_revision=expected_revision,
            decision=decision,
            request_id=request_id,
        )

    def park(
        self,
        opportunity_id: str,
        *,
        expected_revision: int | None = None,
        decision: str | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """暂存（proposed / selected → parked）。"""
        return self._transition(
            opportunity_id,
            OpportunityStatus.PARKED,
            expected_revision=expected_revision,
            decision=decision,
            request_id=request_id,
        )

    def close(
        self,
        opportunity_id: str,
        *,
        expected_revision: int | None = None,
        decision: str | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """本次结束（proposed / ready → closed）。"""
        return self._transition(
            opportunity_id,
            OpportunityStatus.CLOSED,
            expected_revision=expected_revision,
            decision=decision,
            request_id=request_id,
        )

    def mark_ready(
        self,
        opportunity_id: str,
        *,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """成果可审阅（selected → ready）。"""
        return self._transition(
            opportunity_id,
            OpportunityStatus.READY,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    def reopen(
        self,
        opportunity_id: str,
        *,
        expected_revision: int | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        """重新开启（parked / closed → proposed）。"""
        return self._transition(
            opportunity_id,
            OpportunityStatus.PROPOSED,
            expected_revision=expected_revision,
            request_id=request_id,
        )

    def _transition(
        self,
        opportunity_id: str,
        to_status: OpportunityStatus,
        *,
        expected_revision: int | None = None,
        decision: str | None = None,
        request_id: str | None = None,
    ) -> Opportunity:
        with self.repo.locked(opportunity_id):
            opp = self.repo.get(opportunity_id)
            if opp is None:
                raise KeyError(opportunity_id)
            # request_id 幂等：同一请求已应用且快照已推进 → 返回当前快照。
            if request_id is not None:
                applied = self._applied_event(opportunity_id, request_id)
                if applied is not None and opp.revision > applied.expected_revision:
                    return opp
                # 快照仍停在事件前（append_event 与 save 之间崩溃）→ 落下重放修复快照。
            if to_status not in _TRANSITIONS[opp.status]:
                raise ValueError(
                    f"illegal transition: {opp.status.value} -> {to_status.value} "
                    f"for opportunity {opportunity_id}"
                )
            if expected_revision is not None and expected_revision != opp.revision:
                raise OpportunityConflictError(
                    f"revision conflict: expected {expected_revision}, "
                    f"current {opp.revision}"
                )
            new_opp = opp.model_copy(
                update={
                    "status": to_status,
                    "decision": decision if decision is not None else opp.decision,
                    "revision": opp.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            self.repo.append_event(
                OpportunityEvent(
                    event_id=f"{opportunity_id}:r{new_opp.revision}",
                    opportunity_id=opportunity_id,
                    event_type=to_status.value,
                    expected_revision=opp.revision,
                    decision=decision,
                    request_id=request_id,
                )
            )
            self.repo.save(new_opp)
            return new_opp

    def _applied_event(
        self, opportunity_id: str, request_id: str
    ) -> OpportunityEvent | None:
        """事件日志中该 request_id 对应的已应用事件（幂等去重锚点）。"""
        for e in self.repo.list_events(opportunity_id):
            if e.request_id == request_id:
                return e
        return None
