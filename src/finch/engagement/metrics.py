"""互动可观测性与评估指标（关系质量为首要，取代「帖子表现」作为周报核心）。

本模块为纯函数（无 IO）。旧的数量型互动质量指标（compute_metrics / render_metrics）
已随旧互动轨道移除；保留关系质量指标（RelationshipMetrics）与运行级计数。
"""

from datetime import datetime

from pydantic import BaseModel

from finch.content.jobs import ContentJob
from finch.conversations.models import ConversationThread
from finch.conversations.service import ConversationService
from finch.peers.models import PeerProfile, RelationshipStage

from .models import FeedbackSnapshot, InteractionRecord


class RelationshipMetrics(BaseModel):
    """关系质量指标（连接优先改造 Phase 6，取代「帖子表现」作为周报核心）。

    计数型指标，回答「建立了多少真实连接」而非「发了多少内容」。点赞/曝光/粉丝变化
    不在此列（作为辅助数据，不进北极星）。
    """

    meaningful_interactions: int = 0       # 有实质反馈的互动（FeedbackSnapshot.meaningful）
    continued_conversations: int = 0       # 有多于一次互动的对话线索
    repeat_peers: int = 0                  # 互动超过一次的同行数
    new_relevant_peers: int = 0            # 关系阶段已越过 DISCOVERED 的同行数
    ideas_from_conversations: int = 0      # 由对话形成的观点候选数
    collaboration_signals: int = 0         # 含 agreements 或 possible_experiments 的对话数
    stale_conversations: int = 0           # 需要跟进（超期/有未解问题）的对话数
    # Value Discovery P4 diagnosis (sourced counts; not a relationship score).
    bidirectional_threads: int = 0         # 双方均有消息的线索
    help_with_source: int = 0              # usage_feedback 或带 artifact 的实验结果
    open_commitments: int = 0              # 未完成承诺
    presented_opportunities: int = 0
    worth_following: int = 0
    prepared_count: int = 0
    recorded_interactions: int = 0
    active_cross_domain_relationships_30d: int = 0  # north-star (spec §15)


def active_cross_domain_relationships_30d(
    *,
    peers: list[PeerProfile],
    interactions: list[InteractionRecord],
    now: datetime,
    window_days: int = 30,
) -> int:
    """北极星：最近 N 天内双方至少两次有具体内容的互动的人数。

    「有具体内容」= published_body/body 非空。双向用 direction 集合近似；
    若方向未知，则同一 peer ≥2 条 outbound/inbound 记录也计入（用户手工登记）。
    """
    from datetime import timedelta

    cutoff = now - timedelta(days=window_days)
    by_peer: dict[str, list[InteractionRecord]] = {}
    for rec in interactions:
        if rec.occurred_at >= cutoff and (rec.published_body.strip() or rec.body.strip()):
            by_peer.setdefault(rec.peer_id, []).append(rec)

    peer_platforms = {
        p.id: {i.platform for i in p.platform_identities} for p in peers
    }
    count = 0
    for peer_id, recs in by_peer.items():
        if len(recs) < 2:
            continue
        dirs = {r.direction for r in recs}
        bidirectional = "inbound" in dirs and "outbound" in dirs
        multi_touch = len(recs) >= 2
        if not (bidirectional or multi_touch):
            continue
        # Cross-domain: peer has >1 platform identity, or we simply count as
        # cross-domain relationship candidate when peer exists in roster.
        platforms = peer_platforms.get(peer_id, set())
        if len(platforms) >= 1:
            count += 1
    return count


def explain_recommendation_adjustments(
    feedback: list,
) -> list[dict[str, str]]:
    """根据推荐反馈生成可解释的权重调整建议（不静默改分）。

    返回 list[{signal, effect, rationale}]，供周报复盘展示；实际改权需人工确认。
    """
    from finch.engagement.models import (
        ActionFeedbackValue,
        InterestFeedbackValue,
        OutcomeFeedbackValue,
        RecommendationFeedback,
    )

    interests: dict[str, int] = {}
    actions: dict[str, int] = {}
    outcomes: dict[str, int] = {}
    for item in feedback:
        if not isinstance(item, RecommendationFeedback):
            continue
        if item.dimension == "interest":
            interests[item.value] = interests.get(item.value, 0) + 1
        elif item.dimension == "action":
            actions[item.value] = actions.get(item.value, 0) + 1
        elif item.dimension == "outcome":
            outcomes[item.value] = outcomes.get(item.value, 0) + 1

    out: list[dict[str, str]] = []
    # 长期排斥只来自 unsuitable / no_opening；no_time_today（今天没时间）是瞬态信号，
    # 不参与 skip 聚合，避免「没时间」被当成长期排斥。
    skip_n = (
        interests.get(InterestFeedbackValue.UNSUITABLE.value, 0)
        + actions.get(ActionFeedbackValue.NO_OPENING.value, 0)
    )
    accept_n = interests.get(InterestFeedbackValue.WORTH_FOLLOWING.value, 0)
    prepared = actions.get(ActionFeedbackValue.PREPARE.value, 0)
    if skip_n > accept_n and skip_n >= 3:
        out.append(
            {
                "signal": f"skip={skip_n} > accept={accept_n}",
                "effect": "raise min evidence threshold / tighten shortlist",
                "rationale": "用户跳过多于接受，降低低证据推荐",
            }
        )
    if prepared >= 2:
        out.append(
            {
                "signal": f"prepared={prepared}",
                "effect": "boost connection_opportunity weight slightly",
                "rationale": "用户实际准备过互动的类型更有连接价值",
            }
        )
    adopted = outcomes.get(OutcomeFeedbackValue.ADOPTED_REPLIED.value, 0)
    reengaged = outcomes.get(OutcomeFeedbackValue.REENGAGED.value, 0)
    got_info = outcomes.get(OutcomeFeedbackValue.GOT_MORE_INFO.value, 0)
    if adopted >= 1:
        out.append(
            {
                "signal": f"adopted_replied={adopted}",
                "effect": "boost connection_opportunity weight slightly",
                "rationale": "用户采用建议并回复过，这类机会有真实参与价值",
            }
        )
    if reengaged >= 1:
        out.append(
            {
                "signal": f"reengaged={reengaged}",
                "effect": "boost continuity_potential weight slightly",
                "rationale": "发生过再次交流或共同实践，连续性更强",
            }
        )
    if got_info >= 1 and adopted == 0 and reengaged == 0:
        out.append(
            {
                "signal": f"got_more_info={got_info}",
                "effect": "keep current weight",
                "rationale": "得到补充信息但未回复，保持关注不主动加权重",
            }
        )
    if not out:
        out.append(
            {
                "signal": "insufficient_feedback",
                "effect": "no change",
                "rationale": "反馈不足，保持默认权重",
            }
        )
    return out


def compute_relationship_metrics(
    *,
    peers: list[PeerProfile],
    interactions: list[InteractionRecord],
    threads: list[ConversationThread],
    snapshots: list[FeedbackSnapshot],
    jobs: list[ContentJob],
    now: datetime,
    presentation_count: int = 0,
    worth_following_count: int = 0,
    prepared_count: int = 0,
) -> RelationshipMetrics:
    """从关系领域数据聚合关系质量指标（纯函数，无 IO）。"""
    from finch.conversations.models import CommitmentStatus, ObservationKind
    from finch.conversations.service import active_observation_notes

    per_peer: dict[str, int] = {}
    dirs_by_thread_peer: dict[str, set[str]] = {}
    for rec in interactions:
        per_peer[rec.peer_id] = per_peer.get(rec.peer_id, 0) + 1
        dirs_by_thread_peer.setdefault(rec.peer_id, set()).add(rec.direction)

    bidirectional = 0
    help_count = 0
    open_cmts = 0
    for t in threads:
        peer_dirs = dirs_by_thread_peer.get(t.peer_id, set())
        # Approximate bidirectional: both inbound and outbound on same peer,
        # or ≥2 interactions with substance (notes/body).
        if "inbound" in peer_dirs and "outbound" in peer_dirs and len(t.interaction_ids) >= 2:
            bidirectional += 1
        notes = active_observation_notes(t)
        if any(n.kind == ObservationKind.USAGE_FEEDBACK for n in notes):
            help_count += 1
        if any(e.actual_result.strip() and e.artifact_ref for e in t.experiments):
            help_count += 1
        open_cmts += sum(
            1 for c in t.commitments if c.status is CommitmentStatus.OPEN
        )

    svc = ConversationService()
    return RelationshipMetrics(
        meaningful_interactions=sum(1 for s in snapshots if s.meaningful),
        continued_conversations=sum(1 for t in threads if len(t.interaction_ids) >= 2),
        repeat_peers=sum(1 for n in per_peer.values() if n > 1),
        new_relevant_peers=sum(
            1 for p in peers if p.relationship_stage != RelationshipStage.DISCOVERED
        ),
        ideas_from_conversations=sum(1 for j in jobs if j.origin == "conversation"),
        collaboration_signals=sum(1 for t in threads if t.agreements or t.possible_experiments),
        stale_conversations=sum(1 for t in threads if svc.needs_follow_up(t, now=now)),
        bidirectional_threads=bidirectional,
        help_with_source=help_count,
        open_commitments=open_cmts,
        presented_opportunities=presentation_count,
        worth_following=worth_following_count,
        prepared_count=prepared_count,
        recorded_interactions=len(interactions),
        active_cross_domain_relationships_30d=active_cross_domain_relationships_30d(
            peers=peers, interactions=interactions, now=now
        ),
    )


def render_relationship_metrics(m: RelationshipMetrics) -> str:
    """把关系质量指标渲染为 Markdown 列表。"""
    return "\n".join(
        [
            "# Finch Relationship Metrics",
            "",
            "## 关系质量（首要）",
            f"- 有实质反馈的互动: {m.meaningful_interactions}",
            f"- 继续的对话: {m.continued_conversations}",
            f"- 双向有内容线索: {m.bidirectional_threads}",
            f"- 有来源的帮助: {m.help_with_source}",
            f"- 未完成承诺: {m.open_commitments}",
            f"- 重复互动同行: {m.repeat_peers}",
            f"- 新相关同行: {m.new_relevant_peers}",
            f"- 对话形成的观点: {m.ideas_from_conversations}",
            f"- 协作信号: {m.collaboration_signals}",
            f"- 待跟进对话: {m.stale_conversations}",
            "",
            "## 推荐诊断（呈现 → 认可 → 准备 → 记录）",
            f"- 已呈现机会: {m.presented_opportunities}",
            f"- 值得了解反馈: {m.worth_following}",
            f"- 已准备: {m.prepared_count}",
            f"- 已记录互动: {m.recorded_interactions}",
            f"- 30 天持续跨域交流人数: {m.active_cross_domain_relationships_30d}",
        ]
    )
