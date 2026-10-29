"""Solution Explorer（最小版）：选中一个产品 → 8 点理解。

镜像 ``opportunities.assess`` / ``discover`` 的模式：LLM 产语义字段，确定性门禁在代码；
证据引用必须是 ``product.source_refs`` 子集，反馈引用必须是 ``FeedbackReport`` 的
``feedback_id`` 子集，未引用任何证据的 claim 强制标记为假设。

P1 起：把用户反馈报告分层纳入分析（创业者主张 / 用户反馈 / 模型推断），不新增平行系统。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.products.ids import solution_id_for
from finch.products.models import (
    EvidenceStatus,
    FeedbackOpportunity,
    FeedbackOpportunityKind,
    FeedbackReport,
    Product,
    Solution,
    SolutionEvidence,
    SolutionVerdict,
)
from finch.products.repository import SolutionRepository
from finch.sources.models import RawArtifact

_PROMPT = Path("prompts/product-solution.md")
SOLUTION_VERSION = "product-solution-v1"
_TEXT_LIMIT = 2000
_EXCERPT_LIMIT = 500

_REQUIRED_FIELDS = (
    "who_problem",
    "prior_approach",
    "new_solution",
    "why_more_valuable",
    "design_thinking",
    "builders",
)


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _coerce_status(value: str) -> EvidenceStatus:
    try:
        return EvidenceStatus(value)
    except ValueError:
        return EvidenceStatus.ASSUMPTION


def _coerce_kind(value: str) -> FeedbackOpportunityKind | None:
    try:
        return FeedbackOpportunityKind(value)
    except ValueError:
        return None


class FeedbackOpportunityDraft(BaseModel):
    """LLM 输出的反馈机会（enum 用 str 宽容解析）。"""

    kind: str = "pain_point"
    claim: str = ""
    feedback_refs: list[str] = Field(default_factory=list)
    status: str = "assumption"


class SolutionDraft(BaseModel):
    """LLM 方案分析输出（无总分；verdict 用 str 以便宽容解析）。"""

    who_problem: str = ""
    prior_approach: str = ""
    new_solution: str = ""
    why_more_valuable: str = ""
    value_evidence: list[SolutionEvidence] = Field(default_factory=list)
    design_thinking: str = ""
    builders: str = ""
    verdict: str = "watch"
    feedback_opportunities: list[FeedbackOpportunityDraft] = Field(default_factory=list)
    eval_failed: bool = False
    skip_reason: str = ""


def render_product_json(product: Product, artifacts: list[RawArtifact]) -> str:
    return json.dumps(
        {
            "key": product.key,
            "name": product.name,
            "tagline": product.tagline,
            "founder": product.founder.display_name if product.founder else "",
            "target_user": product.target_user,
            "problem_solved": product.problem_solved,
            "core_solution": product.core_solution,
            "differentiation": product.differentiation,
            "progress": product.progress,
            "artifacts": [
                {"artifact_id": a.artifact_id, "title": a.title or "", "text": _truncate(a.text)}
                for a in artifacts
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def render_feedback_json(report: FeedbackReport | None) -> str:
    """把反馈报告压缩成 Solution prompt 可消费的 JSON（逐条可追溯到 feedback_id）。"""
    if report is None:
        return json.dumps({"feedback": [], "report": {}}, ensure_ascii=False, indent=2)
    return json.dumps(
        {
            "report_id": report.report_id,
            "feedback": [
                {
                    "feedback_id": f.feedback_id,
                    "source": f.source.value,
                    "source_url": f.source_url,
                    "feedback_type": f.feedback_type.value,
                    "user_scenario": f.user_scenario,
                    "user_problem": f.user_problem,
                    "requested_outcome": f.requested_outcome,
                    "experience_level": f.experience_level.value,
                    "original_excerpt": _truncate(f.original_excerpt, _EXCERPT_LIMIT),
                }
                for f in report.feedback
            ],
            "recurring_problems": [
                {"claim": p.claim, "count": p.count} for p in report.recurring_problems
            ],
            "unmet_needs": [{"claim": p.claim, "count": p.count} for p in report.unmet_needs],
            "contradictions": [{"claim": p.claim} for p in report.contradictions],
            "info_gaps": report.info_gaps,
        },
        ensure_ascii=False,
        indent=2,
    )


def assess_solution(
    runner: StructuredInferenceRunner,
    product: Product,
    artifacts: list[RawArtifact],
    feedback_report: FeedbackReport | None = None,
) -> SolutionDraft:
    """LLM 产出 8 点理解；失败 → eval_failed（fail-soft）。"""
    prompt = _PROMPT.read_text().format(
        product_json=render_product_json(product, artifacts),
        feedback_json=render_feedback_json(feedback_report),
    )
    try:
        return cast(SolutionDraft, runner.run(prompt, SolutionDraft))
    except Exception:
        return SolutionDraft(eval_failed=True, skip_reason="方案分析失败（LLM 异常）")


def build_solution(
    draft: SolutionDraft,
    product: Product,
    *,
    version: str = SOLUTION_VERSION,
    feedback_report: FeedbackReport | None = None,
) -> Solution | None:
    """确定性把 draft 转为 Solution；核心叙事字段缺失 → None。

    证据门禁：source_refs ⊆ product.source_refs；feedback_refs ⊆ 报告 feedback_id 子集；
    两者皆无 → 强制 ASSUMPTION + origin=inference。
    """
    if draft.eval_failed:
        return None
    for field in _REQUIRED_FIELDS:
        if not getattr(draft, field, "").strip():
            return None

    try:
        verdict = SolutionVerdict(draft.verdict)
    except ValueError:
        verdict = SolutionVerdict.WATCH

    allowed_source = set(product.source_refs)
    allowed_feedback = (
        {f.feedback_id for f in feedback_report.feedback} if feedback_report else set()
    )

    evidence: list[SolutionEvidence] = []
    for ev in draft.value_evidence:
        src_refs = [r for r in ev.source_refs if r in allowed_source]
        fb_refs = [r for r in ev.feedback_refs if r in allowed_feedback]
        if src_refs or fb_refs:
            origin = "feedback" if fb_refs and not src_refs else "founder"
            evidence.append(
                ev.model_copy(
                    update={"source_refs": src_refs, "feedback_refs": fb_refs, "origin": origin}
                )
            )
        else:
            # 未引用任何产品/反馈证据的 claim 只能算假设，不得伪装成显式证据。
            evidence.append(
                ev.model_copy(
                    update={
                        "source_refs": [],
                        "feedback_refs": [],
                        "origin": "inference",
                        "status": EvidenceStatus.ASSUMPTION,
                    }
                )
            )

    opportunities: list[FeedbackOpportunity] = []
    for d in draft.feedback_opportunities:
        kind = _coerce_kind(d.kind)
        if kind is None:
            continue
        refs = [r for r in d.feedback_refs if r in allowed_feedback]
        status = _coerce_status(d.status) if refs else EvidenceStatus.ASSUMPTION
        opportunities.append(
            FeedbackOpportunity(kind=kind, claim=d.claim.strip(), feedback_refs=refs, status=status)
        )

    feedback_fingerprint = feedback_report.report_id if feedback_report else ""
    return Solution(
        id=solution_id_for(
            product.id,
            product.fingerprint,
            version=version,
            feedback_fingerprint=feedback_fingerprint,
        ),
        product_id=product.id,
        version=version,
        who_problem=draft.who_problem.strip(),
        prior_approach=draft.prior_approach.strip(),
        new_solution=draft.new_solution.strip(),
        why_more_valuable=draft.why_more_valuable.strip(),
        value_evidence=evidence,
        design_thinking=draft.design_thinking.strip(),
        builders=draft.builders.strip(),
        verdict=verdict,
        feedback_opportunities=opportunities,
        feedback_report_id=feedback_report.report_id if feedback_report else "",
        sources=list(product.source_refs),
    )


def explore_product(
    runner: StructuredInferenceRunner,
    product: Product,
    artifacts: list[RawArtifact],
    repository: SolutionRepository,
    *,
    version: str = SOLUTION_VERSION,
    force: bool = False,
    feedback_report: FeedbackReport | None = None,
) -> Solution | None:
    """选中产品 → 8 点理解；幂等（同 fingerprint + 反馈报告 + 版本不重复调 LLM）。

    ``force`` 强制重评。反馈内容变化 → ``report_id`` 变化 → 新 solution_id → 自动重评。
    """
    feedback_fingerprint = feedback_report.report_id if feedback_report else ""
    expected_id = solution_id_for(
        product.id, product.fingerprint, version=version, feedback_fingerprint=feedback_fingerprint
    )
    if not force:
        existing = repository.get(product.id)
        if existing is not None and existing.id == expected_id:
            return existing

    draft = assess_solution(runner, product, artifacts, feedback_report=feedback_report)
    solution = build_solution(draft, product, version=version, feedback_report=feedback_report)
    if solution is None:
        return None
    repository.upsert(solution)
    return solution
