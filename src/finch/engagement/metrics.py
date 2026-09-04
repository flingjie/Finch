"""互动可观测性与评估指标（执行计划 Phase 7）。

质量评估不以互动数量为核心（执行计划 6「质量评估不以互动数量为核心」）。首要指标是：

1. 用户愿意批准或只需少量修改的草稿比例 → ``draft_approval_rate`` + ``user_edit_distance``；
2. 互动是否获得实质回复 → ``meaningful_response_rate``；
3. 互动是否产生后续实验、判断或原创内容证据 → ``conversation_to_personal_evidence_rate``。

``interactions_executed`` 与 ``duplicate_or_low_value_rate`` 是数量/质量辅助信号，不是
优化目标。

运行级/成本信号（不在本模块计算，见 :class:`~finch.engagement.models.EngagementRunStats`）：

- ``no_evidence_runs`` / ``posts_scanned`` / 单轮 ``latency_ms`` 由
  ``EngagementRunStats`` 记录，经 ``summarize_run_stats`` 聚合，供 CLI
  ``finch engagement metrics`` 输出；
- 模型成本需要 CodexRunner 做 per-run token/cost 埋点，本阶段明确 OUT of scope，留作后续。

本模块全部为纯函数（无 IO），可直接单元测试。
"""

import difflib

from pydantic import BaseModel

from .models import (
    ConversationEvidence,
    EngagementRunStats,
    FeedbackSnapshot,
    InteractionAction,
    InteractionCandidate,
    InteractionStatus,
)


class EngagementMetrics(BaseModel):
    """互动质量指标汇总（比例均为 0.0–1.0；无数据时取 0.0，非 NaN）。"""

    interactions_executed: int = 0
    draft_approval_rate: float = 0.0
    user_edit_distance: float = 0.0
    meaningful_response_rate: float = 0.0
    conversation_to_personal_evidence_rate: float = 0.0
    duplicate_or_low_value_rate: float = 0.0


class RunStatsSummary(BaseModel):
    """运行级计数汇总（与质量指标互补，回答「扫了多少、跑了多少轮、多少轮无证据」）。"""

    total_posts_scanned: int = 0
    total_runs: int = 0
    no_evidence_runs: int = 0


def _edit_distance(a: str, b: str) -> float:
    """基于 difflib.SequenceMatcher 的归一化编辑距离（0.0=完全相同，1.0=完全不同）。"""
    if a == b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b).ratio()


def compute_metrics(
    interactions: list[InteractionCandidate],
    feedback: list[FeedbackSnapshot],
    evidence: list[ConversationEvidence],
) -> EngagementMetrics:
    """从持久化数据聚合互动质量指标（纯函数，无 IO）。

    - ``interactions_executed``：status == EXECUTED 的候选数；
    - ``draft_approval_rate``：需要批准的草稿候选（``approval_required=True``）中被批准
      （APPROVED/EXECUTED）的比例；
    - ``user_edit_distance``：有 ``revised_draft`` 的候选上 ``draft`` → ``revised_draft``
      的平均归一化编辑距离（无编辑则 0.0）；
    - ``meaningful_response_rate``：``meaningful=True`` 快照 / 全部快照；
    - ``conversation_to_personal_evidence_rate``：``verified=True`` 证据 / 全部 conversation 证据；
    - ``duplicate_or_low_value_rate``：``IGNORE`` 动作或 ``REJECTED`` 状态的候选 / 全部候选。

    所有比例无分母时为 0.0（不产生 NaN / ZeroDivisionError）。
    """
    executed = sum(1 for c in interactions if c.status == InteractionStatus.EXECUTED)

    proposed = [c for c in interactions if c.approval_required]
    accepted = sum(
        1
        for c in proposed
        if c.status == InteractionStatus.APPROVED or c.status == InteractionStatus.EXECUTED
    )
    draft_approval_rate = accepted / len(proposed) if proposed else 0.0

    distances: list[float] = []
    for c in interactions:
        if c.draft is not None and c.revised_draft is not None:
            distances.append(_edit_distance(c.draft, c.revised_draft))
    user_edit_distance = sum(distances) / len(distances) if distances else 0.0

    meaningful = sum(1 for s in feedback if s.meaningful)
    meaningful_response_rate = meaningful / len(feedback) if feedback else 0.0

    verified = sum(1 for e in evidence if e.verified)
    conversation_to_personal_evidence_rate = verified / len(evidence) if evidence else 0.0

    low_value = sum(
        1
        for c in interactions
        if c.action == InteractionAction.IGNORE or c.status == InteractionStatus.REJECTED
    )
    duplicate_or_low_value_rate = low_value / len(interactions) if interactions else 0.0

    return EngagementMetrics(
        interactions_executed=executed,
        draft_approval_rate=draft_approval_rate,
        user_edit_distance=user_edit_distance,
        meaningful_response_rate=meaningful_response_rate,
        conversation_to_personal_evidence_rate=conversation_to_personal_evidence_rate,
        duplicate_or_low_value_rate=duplicate_or_low_value_rate,
    )


def render_metrics(m: EngagementMetrics) -> str:
    """把 EngagementMetrics 渲染为 Markdown（镜像 render_weekly 风格）。"""
    return "\n".join(
        [
            "# Finch Engagement Metrics",
            "",
            "## 质量指标（首要）",
            f"- 草稿批准率: {m.draft_approval_rate:.0%}",
            f"- 用户编辑距离: {m.user_edit_distance:.3f}",
            f"- 实质回复率: {m.meaningful_response_rate:.0%}",
            f"- conversation→personal 证据率: {m.conversation_to_personal_evidence_rate:.0%}",
            f"- 重复/低价值率: {m.duplicate_or_low_value_rate:.0%}",
            "",
            "## 数量指标（辅助，非优化目标）",
            f"- 已执行互动: {m.interactions_executed}",
        ]
    )


def summarize_run_stats(stats: list[EngagementRunStats]) -> RunStatsSummary:
    """聚合运行级计数；``no_evidence_runs`` = posts_scanned == 0 的运行数。"""
    return RunStatsSummary(
        total_posts_scanned=sum(s.posts_scanned for s in stats),
        total_runs=len(stats),
        no_evidence_runs=sum(1 for s in stats if s.posts_scanned == 0),
    )


def render_run_stats(s: RunStatsSummary) -> str:
    """渲染运行级计数（含 no_evidence_runs / posts_scanned / 运行次数）。"""
    return "\n".join(
        [
            "",
            "## 运行级计数",
            f"- 累计扫描帖子: {s.total_posts_scanned}",
            f"- 运行次数: {s.total_runs}",
            f"- 无证据运行: {s.no_evidence_runs}",
        ]
    )
