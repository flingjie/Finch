"""连接闭环：机会判断、回复草稿、关系复盘（不调用 OpenCLI 写操作）。"""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from finch.peers.models import PeerProfile, RelationshipStage
from finch.peers.person import CreatorEvidence


class ConnectionDecision(StrEnum):
    CONNECT = "connect"
    SKIP = "SKIP"
    DEFER = "defer"


class ConnectionOpportunity(BaseModel):
    """连接机会：对方在做什么、用户能贡献什么、为何现在。"""

    opportunity_id: str
    person_id: str
    peer_id: str
    their_problem: str
    user_contribution: str
    why_now: str
    why_not: str = ""
    min_action: str = ""
    decision: ConnectionDecision = ConnectionDecision.CONNECT
    user_evidence_refs: list[str] = Field(default_factory=list)
    their_artifact_ids: list[str] = Field(default_factory=list)


class ReplyDraft(BaseModel):
    """回复草稿：观察 → 真实经验 → 可继续问题；永不自动发布。"""

    draft_id: str
    opportunity_id: str
    peer_id: str
    observation: str
    user_experience: str
    question: str
    full_text: str
    decision: ConnectionDecision
    user_evidence_refs: list[str] = Field(default_factory=list)
    skip_reason: str = ""


# 亲历主张标记：只有出现这类主张时才要求对应个人证据（D3）。
_FIRSTHAND_MARKERS = (
    "我用过",
    "我测试过",
    "我们曾经",
    "我试过",
    "我们做过",
    "我部署过",
    "我跑过",
    "i've used",
    "i used",
    "i tested",
    "we once",
    "we used",
)


def _claims_firsthand(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in _FIRSTHAND_MARKERS)


class RelationshipReview(BaseModel):
    """关系复盘：是否有自然后续理由。"""

    peer_id: str
    person_id: str
    current_stage: RelationshipStage
    suggested_stage: RelationshipStage
    natural_next_reason: str = ""
    should_contact: bool = False
    dormant_reason: str = ""
    not_progress_signals: list[str] = Field(default_factory=list)


def build_connection_opportunity(
    *,
    peer: PeerProfile,
    person_id: str,
    their_artifacts: list[str],
    their_summary: str,
    user_evidence_refs: list[str],
    user_contribution: str,
) -> ConnectionOpportunity:
    """无用户贡献点/问题时返回 SKIP；仅当声称亲历才要求个人证据（D3）。"""
    oid = f"conn_{uuid4().hex[:12]}"
    contribution = user_contribution.strip()
    if not contribution:
        return ConnectionOpportunity(
            opportunity_id=oid,
            person_id=person_id,
            peer_id=peer.id,
            their_problem=their_summary,
            user_contribution="",
            why_now="",
            why_not="没有实质观察或具体问题",
            decision=ConnectionDecision.SKIP,
            their_artifact_ids=their_artifacts,
        )
    if _claims_firsthand(contribution) and not user_evidence_refs:
        return ConnectionOpportunity(
            opportunity_id=oid,
            person_id=person_id,
            peer_id=peer.id,
            their_problem=their_summary,
            user_contribution=contribution,
            why_now="",
            why_not="声称亲历但没有个人证据",
            decision=ConnectionDecision.SKIP,
            their_artifact_ids=their_artifacts,
        )
    return ConnectionOpportunity(
        opportunity_id=oid,
        person_id=person_id,
        peer_id=peer.id,
        their_problem=their_summary,
        user_contribution=contribution,
        why_now="对方近期公开讨论该问题，且用户有具体观察或可贡献的经验",
        min_action="公开回复并诚实提问或补充自己的真实经验",
        decision=ConnectionDecision.CONNECT,
        user_evidence_refs=list(user_evidence_refs),
        their_artifact_ids=their_artifacts,
    )


def craft_reply(
    opportunity: ConnectionOpportunity,
    *,
    observation: str,
    user_experience: str,
    question: str,
) -> ReplyDraft:
    """草稿结构：具体观察 →（可选）真实经验 → 一个可继续讨论的问题。

    允许「具体观察 + 诚实提问」而不必附亲历经验；只有声称亲历时才要求个人证据。
    禁止纯赞美、禁止假装用过、禁止补造用户经历。
    """
    draft_id = f"reply_{uuid4().hex[:12]}"
    if opportunity.decision == ConnectionDecision.SKIP:
        return ReplyDraft(
            draft_id=draft_id,
            opportunity_id=opportunity.opportunity_id,
            peer_id=opportunity.peer_id,
            observation="",
            user_experience="",
            question="",
            full_text="",
            decision=ConnectionDecision.SKIP,
            skip_reason=opportunity.why_not or "SKIP",
        )
    obs = observation.strip()
    exp = user_experience.strip()
    q = question.strip()

    # 具体观察 + 诚实提问（无亲历）→ 允许。
    if obs and q and not exp:
        return ReplyDraft(
            draft_id=draft_id,
            opportunity_id=opportunity.opportunity_id,
            peer_id=opportunity.peer_id,
            observation=obs,
            user_experience="",
            question=q,
            full_text=f"{obs}\n\n{q}".strip(),
            decision=ConnectionDecision.CONNECT,
        )

    if exp:
        # 亲历主张无证据 → SKIP。
        if _claims_firsthand(exp) and not opportunity.user_evidence_refs:
            return ReplyDraft(
                draft_id=draft_id,
                opportunity_id=opportunity.opportunity_id,
                peer_id=opportunity.peer_id,
                observation="",
                user_experience="",
                question="",
                full_text="",
                decision=ConnectionDecision.SKIP,
                skip_reason="claimed firsthand without evidence",
            )
        # 纯赞美 → SKIP。
        praise_markers = ("太棒了", "amazing", "great post", "爱了", "强")
        if any(exp.lower() == m.lower() for m in praise_markers):
            return ReplyDraft(
                draft_id=draft_id,
                opportunity_id=opportunity.opportunity_id,
                peer_id=opportunity.peer_id,
                observation=obs,
                user_experience="",
                question="",
                full_text="",
                decision=ConnectionDecision.SKIP,
                skip_reason="praise-only",
                user_evidence_refs=opportunity.user_evidence_refs,
            )
        q = q or "你这边后来怎么验证的？"
        return ReplyDraft(
            draft_id=draft_id,
            opportunity_id=opportunity.opportunity_id,
            peer_id=opportunity.peer_id,
            observation=obs,
            user_experience=exp,
            question=q,
            full_text=f"{obs}\n\n{exp}\n\n{q}".strip(),
            decision=ConnectionDecision.CONNECT,
            user_evidence_refs=list(opportunity.user_evidence_refs),
        )

    # 没有观察或提问 → SKIP。
    return ReplyDraft(
        draft_id=draft_id,
        opportunity_id=opportunity.opportunity_id,
        peer_id=opportunity.peer_id,
        observation="",
        user_experience="",
        question="",
        full_text="",
        decision=ConnectionDecision.SKIP,
        skip_reason="no observation or question",
    )


def review_relationship(
    peer: PeerProfile,
    *,
    person_id: str,
    bidirectional_exchanges: int = 0,
    started_experiment: bool = False,
    shared_visible_outcome: bool = False,
    natural_next_reason: str = "",
    stale_days: int = 0,
) -> RelationshipReview:
    """根据互动证据建议阶段；无新理由则不应联系。"""
    current = RelationshipStage.normalize(peer.relationship_stage)
    suggested = current
    not_progress: list[str] = []

    if bidirectional_exchanges >= 1 and current == RelationshipStage.DISCOVERED:
        suggested = RelationshipStage.ENGAGED
    if bidirectional_exchanges >= 2:
        suggested = RelationshipStage.RECURRING
    if started_experiment:
        suggested = RelationshipStage.PRACTICING
    if shared_visible_outcome:
        suggested = RelationshipStage.COLLABORATING

    should_contact = bool(natural_next_reason.strip())
    dormant_reason = ""
    if not should_contact and stale_days >= 30 and current not in {
        RelationshipStage.DISCOVERED,
        RelationshipStage.DORMANT,
    }:
        suggested = RelationshipStage.DORMANT
        dormant_reason = "无新的自然后续理由，暂转为 dormant"
        not_progress.append("time elapsed alone is not a reason to message")

    if not natural_next_reason.strip():
        not_progress.append("no new contribution or shared question")

    return RelationshipReview(
        peer_id=peer.id,
        person_id=person_id,
        current_stage=current,
        suggested_stage=suggested,
        natural_next_reason=natural_next_reason,
        should_contact=should_contact,
        dormant_reason=dormant_reason,
        not_progress_signals=not_progress,
    )


def apply_stage_upgrade(
    peer: PeerProfile, review: RelationshipReview
) -> PeerProfile:
    """仅当 review 建议更高/休眠阶段时更新；不自动发消息。"""
    return peer.model_copy(update={"relationship_stage": review.suggested_stage})


def evidence_from_artifacts(
    *,
    person_id: str,
    peer_id: str,
    artifact_ids: list[str],
    kind_hint: str = "knowledge_sharing",
) -> list[CreatorEvidence]:
    """从 artifact 列表构造最小 CreatorEvidence（供 skill 输入）。"""
    from finch.peers.person import CreatorEvidenceKind

    kind = CreatorEvidenceKind(kind_hint)
    out: list[CreatorEvidence] = []
    for i, aid in enumerate(artifact_ids):
        out.append(
            CreatorEvidence(
                evidence_id=f"ce_{uuid4().hex[:10]}_{i}",
                person_id=person_id,
                peer_id=peer_id,
                artifact_id=aid,
                kind=kind,
                claim=f"artifact {aid}",
                support=[aid],
                first_hand=kind
                in {
                    CreatorEvidenceKind.FIRST_HAND_EXPERIENCE,
                    CreatorEvidenceKind.CREATION,
                },
                confidence=0.6,
            )
        )
    return out
