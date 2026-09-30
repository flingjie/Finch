"""机会漏斗指标（规范 §14.2）：原始次数，不合成单一关系分。

五个过程信号：呈现首选 → 选中 → 可审阅成果 → 用户实际参与 → 实质回应 / 再次交流。
「呈现首选」优先用 ``PresentationRecord``（文本前台实际展示）；无呈现记录时回退到
快照中的 ``preferred_opportunity_id``（本轮发现写入但未必已展示）。
解释限制写在渲染文案里：小样本、时机与外部可见性影响大。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from finch.engagement.models import (
    DiscoverySnapshot,
    FeedbackSnapshot,
    InteractionRecord,
    PresentationRecord,
)
from finch.opportunities.models import Opportunity, OpportunityStatus
from finch.opportunities.repository import OpportunityRepository


class OpportunityFunnel(BaseModel):
    """机会漏斗原始计数（规范 §14.2）。"""

    preferred_presented: int = 0  # 实际展示的首选（PresentationRecord 优先）
    selected: int = 0  # selected 事件数（时间窗内）
    ready: int = 0  # ready 事件数（时间窗内）
    outbound_participated: int = 0  # 关联 opportunity_id 的 outbound 互动
    meaningful_replies: int = 0  # 实质回应（FeedbackSnapshot.meaningful 或 outcome）
    repeat_meaningful_peers: int = 0  # 出现再次实质交流的人数
    # 案例引用（最多各 3 个 id），便于周报展示
    preferred_ids: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    ready_ids: list[str] = Field(default_factory=list)
    outbound_ids: list[str] = Field(default_factory=list)
    meaningful_ids: list[str] = Field(default_factory=list)


def compute_opportunity_funnel(
    *,
    snapshots: list[DiscoverySnapshot],
    opportunities: list[Opportunity],
    opportunity_repo: OpportunityRepository,
    interactions: list[InteractionRecord],
    feedbacks: list[FeedbackSnapshot],
    presentations: list[PresentationRecord] | None = None,
    since: datetime | None = None,
) -> OpportunityFunnel:
    """从呈现 / 快照 / 事件 / 互动聚合漏斗原始次数（纯聚合，无 LLM）。"""
    preferred_ids: list[str] = []
    if presentations:
        for p in presentations:
            if since is not None and p.presented_at < since:
                continue
            if p.opportunity_id:
                preferred_ids.append(p.opportunity_id)
    else:
        for s in snapshots:
            if since is not None and s.created_at < since:
                continue
            if s.preferred_opportunity_id:
                preferred_ids.append(s.preferred_opportunity_id)

    selected_ids: list[str] = []
    ready_ids: list[str] = []
    for opp in opportunities:
        for e in opportunity_repo.list_events(opp.id):
            if since is not None and e.created_at < since:
                continue
            if e.event_type == OpportunityStatus.SELECTED.value:
                selected_ids.append(opp.id)
            elif e.event_type == OpportunityStatus.READY.value:
                ready_ids.append(opp.id)

    outbound_ids: list[str] = []
    for r in interactions:
        if since is not None and r.occurred_at < since:
            continue
        if r.direction == "outbound" and r.opportunity_id:
            outbound_ids.append(r.id)

    meaningful_by_interaction = {
        f.interaction_id for f in feedbacks if f.meaningful
    }
    meaningful_ids: list[str] = []
    meaningful_peer_counts: dict[str, int] = {}
    for r in interactions:
        if since is not None and r.occurred_at < since:
            continue
        is_meaningful = (
            r.id in meaningful_by_interaction or r.outcome == "meaningful"
        )
        if is_meaningful:
            meaningful_ids.append(r.id)
            meaningful_peer_counts[r.peer_id] = (
                meaningful_peer_counts.get(r.peer_id, 0) + 1
            )

    return OpportunityFunnel(
        preferred_presented=len(preferred_ids),
        selected=len(selected_ids),
        ready=len(ready_ids),
        outbound_participated=len(outbound_ids),
        meaningful_replies=len(meaningful_ids),
        repeat_meaningful_peers=sum(
            1 for n in meaningful_peer_counts.values() if n >= 2
        ),
        preferred_ids=list(dict.fromkeys(preferred_ids))[:3],
        selected_ids=list(dict.fromkeys(selected_ids))[:3],
        ready_ids=list(dict.fromkeys(ready_ids))[:3],
        outbound_ids=list(dict.fromkeys(outbound_ids))[:3],
        meaningful_ids=list(dict.fromkeys(meaningful_ids))[:3],
    )


def render_opportunity_funnel(f: OpportunityFunnel) -> str:
    """渲染漏斗原始次数与解释限制（不合成单一关系分）。"""
    return "\n".join(
        [
            "## 机会漏斗（原始次数）",
            f"- 呈现首选: {f.preferred_presented}"
            + (f"（例: {', '.join(f.preferred_ids)}）" if f.preferred_ids else ""),
            f"- 用户选定: {f.selected}"
            + (f"（例: {', '.join(f.selected_ids)}）" if f.selected_ids else ""),
            f"- 可审阅成果: {f.ready}"
            + (f"（例: {', '.join(f.ready_ids)}）" if f.ready_ids else ""),
            f"- 用户实际参与: {f.outbound_participated}"
            + (f"（例: {', '.join(f.outbound_ids)}）" if f.outbound_ids else ""),
            f"- 实质回应: {f.meaningful_replies}"
            + (f"（例: {', '.join(f.meaningful_ids)}）" if f.meaningful_ids else ""),
            f"- 再次实质交流人数: {f.repeat_meaningful_peers}",
            "",
            "（解释限制：小样本、时机与外部可见性影响大；点赞/礼貌不算实质回应。）",
        ]
    )
