"""首选机会发现：把一个人 + 其近期成果转成一条首选机会并落库（规范 §5/§6）。

``discover_preferred_opportunity`` 是 daily 发现链路的接线点：对首选候选做一次 LLM 机会
判断（``assess_opportunity``），确定性转成 ``Opportunity`` 聚合并落库；已存在则返回既有
（幂等，不重复调 LLM），无贡献点则返回 None。

机会 ID 关联「具体讨论 + 切入点」而非人物：``person_ref`` 表示身份，ID 带内容/问题的
稳定指纹。来源内容或用户问题变化 → 新指纹 → 重新评估；同一指纹已存在但已关闭/暂存 →
不直接作为今日首选重新出现。同指纹已跳过（``SkipAssessment``）→ 不重复调 LLM。
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.assess import assess_opportunity, build_opportunity
from finch.opportunities.models import Opportunity, OpportunityStatus, SkipAssessment
from finch.opportunities.repository import SkipAssessmentRepository
from finch.opportunities.service import OpportunityService
from finch.sources.models import RawArtifact

_TEXT_LIMIT = 600


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def render_artifacts_json(artifacts: list[RawArtifact]) -> str:
    """把候选的近期成果渲染为 prompt 用的 JSON 字符串（截断长文本）。"""
    return json.dumps(
        [
            {
                "artifact_id": a.artifact_id,
                "title": a.title or "",
                "text": _truncate(a.text),
                "url": a.canonical_url,
            }
            for a in artifacts
        ],
        ensure_ascii=False,
        indent=2,
    )


@dataclass
class DiscoverOutcome:
    """首选机会发现结果：区分推荐 / 跳过 / 评估失败三种结果。"""

    opportunity: Opportunity | None
    outcome: Literal["recommended", "skipped", "eval_failed"]
    reason: str = ""
    fingerprint: str = ""
    opportunity_id: str = ""


def opportunity_context_fingerprint(
    *,
    person_ref: str,
    current_work: str,
    why_relevant: str,
    artifacts: list[RawArtifact],
    user_context: str,
    user_practices: str = "",
) -> str:
    """对「人物 + 当前材料 + 用户问题 + 已确认实践」做稳定指纹；任一变化 → 新指纹。"""
    artifact_keys = sorted(
        (
            a.artifact_id
            + ":"
            + (
                a.content_fingerprint
                or hashlib.sha256((a.text or "").encode("utf-8")).hexdigest()
            )
        )
        for a in artifacts
    )
    parts = [person_ref, current_work, why_relevant, user_context]
    if user_practices:  # 仅非空时加入，保持既有 opp_* id 稳定
        parts.append(user_practices)
    raw = "\n".join([*parts, *artifact_keys])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def discover_preferred_opportunity_outcome(
    *,
    runner: StructuredInferenceRunner,
    peer_id: str,
    display_name: str,
    platform: str,
    current_work: str,
    why_relevant: str,
    person_ref: str,
    artifacts: list[RawArtifact],
    service: OpportunityService,
    user_context: str = "",
    user_practices: str = "",
    skips: SkipAssessmentRepository | None = None,
) -> DiscoverOutcome:
    """对首选候选做机会判断并落库；幂等（同指纹已存在 / 已跳过返回既有），无贡献点 → 跳过。"""
    fingerprint = opportunity_context_fingerprint(
        person_ref=person_ref,
        current_work=current_work,
        why_relevant=why_relevant,
        artifacts=artifacts,
        user_context=user_context,
        user_practices=user_practices,
    )
    opportunity_id = f"opp_{person_ref}_{fingerprint}"
    existing = service.get(opportunity_id)
    if existing is not None:
        if existing.status in (OpportunityStatus.CLOSED, OpportunityStatus.PARKED):
            return DiscoverOutcome(
                None,
                "skipped",
                f"机会已{existing.status.value}，材料未变化",
                fingerprint=fingerprint,
                opportunity_id=opportunity_id,
            )
        return DiscoverOutcome(
            existing,
            "recommended",
            fingerprint=fingerprint,
            opportunity_id=opportunity_id,
        )

    # 同指纹已跳过：不重复调 LLM（eval_failed 不缓存）。
    if skips is not None:
        cached = skips.get(opportunity_id)
        if cached is not None:
            return DiscoverOutcome(
                None,
                "skipped",
                cached.reason or "无最小贡献",
                fingerprint=fingerprint,
                opportunity_id=opportunity_id,
            )

    draft = assess_opportunity(
        runner,
        peer_id=peer_id,
        display_name=display_name,
        platform=platform,
        current_work=current_work,
        why_relevant=why_relevant,
        their_artifacts_json=render_artifacts_json(artifacts),
        user_context=user_context,
        user_practices=user_practices,
    )
    if draft.eval_failed:
        return DiscoverOutcome(
            None,
            "eval_failed",
            draft.skip_reason,
            fingerprint=fingerprint,
            opportunity_id=opportunity_id,
        )
    if not draft.recommend or not draft.contribution.strip():
        reason = draft.skip_reason or "无最小贡献"
        if skips is not None:
            skips.save(
                SkipAssessment(
                    opportunity_id=opportunity_id,
                    person_ref=person_ref,
                    fingerprint=fingerprint,
                    reason=reason,
                )
            )
        return DiscoverOutcome(
            None,
            "skipped",
            reason,
            fingerprint=fingerprint,
            opportunity_id=opportunity_id,
        )
    opp = build_opportunity(draft, opportunity_id=opportunity_id, person_ref=person_ref)
    if opp is None:
        reason = "无最小贡献"
        if skips is not None:
            skips.save(
                SkipAssessment(
                    opportunity_id=opportunity_id,
                    person_ref=person_ref,
                    fingerprint=fingerprint,
                    reason=reason,
                )
            )
        return DiscoverOutcome(
            None,
            "skipped",
            reason,
            fingerprint=fingerprint,
            opportunity_id=opportunity_id,
        )
    return DiscoverOutcome(
        service.create_from(opp),
        "recommended",
        fingerprint=fingerprint,
        opportunity_id=opportunity_id,
    )


def discover_preferred_opportunity(
    *,
    runner: StructuredInferenceRunner,
    peer_id: str,
    display_name: str,
    platform: str,
    current_work: str,
    why_relevant: str,
    person_ref: str,
    artifacts: list[RawArtifact],
    service: OpportunityService,
    user_context: str = "",
    user_practices: str = "",
    skips: SkipAssessmentRepository | None = None,
) -> Opportunity | None:
    """兼容包装：返回首选机会或 None（详见 ``discover_preferred_opportunity_outcome``）。"""
    return discover_preferred_opportunity_outcome(
        runner=runner,
        peer_id=peer_id,
        display_name=display_name,
        platform=platform,
        current_work=current_work,
        why_relevant=why_relevant,
        person_ref=person_ref,
        artifacts=artifacts,
        service=service,
        user_context=user_context,
        user_practices=user_practices,
        skips=skips,
    ).opportunity
