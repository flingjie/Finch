"""贡献制作（规范 §6）：选定机会后按需制作方法卡/回复草稿/演示说明等。

``write_contribution`` 是纯写作步骤：用 ``prompts/prepare-contribution.md`` 让模型按机会的
``proposal``（最小贡献 + 形式 + 预期输出 + 范围）产出正文。可注入 voice 摘要与已确认
AuthorPosition；正文是可审阅表达方案，不是作者已确认的立场。材料来源与执行状态由代码侧
``Artifact`` 的 ``material_origin`` / ``execution_status`` 记录，模型不得声称「已运行」或
虚构个人经历。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.voice import VoiceProfile
from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.models import (
    Artifact,
    ArtifactKind,
    ContributionForm,
    EvidenceRef,
    ExecutionStatus,
    MaterialOrigin,
    Opportunity,
    OpportunityStatus,
)
from finch.opportunities.service import OpportunityService

_PROMPT = Path("prompts/prepare-contribution.md")

_FORM_TO_KIND: dict[ContributionForm, ArtifactKind] = {
    ContributionForm.REPLY_DRAFT: ArtifactKind.REPLY_DRAFT,
    ContributionForm.METHOD_CARD: ArtifactKind.METHOD_CARD,
    ContributionForm.DEMO: ArtifactKind.DEMO,
    ContributionForm.CASE: ArtifactKind.CASE,
    ContributionForm.CLARIFYING_QUESTION: ArtifactKind.REPLY_DRAFT,
}


class ContributionBodyOutput(BaseModel):
    """贡献正文输出：只回传 body。"""

    body: str


@dataclass
class PreparedArtifact:
    """可审阅成果：正文 + 来源 + 执行状态（与 Artifact 元数据一一对应）。"""

    id: str
    kind: ArtifactKind
    body: str
    source_refs: list[str]
    execution_status: ExecutionStatus


@dataclass
class PreparedContribution:
    """``prepare_contribution`` 的结果：机会（ready）+ 可直接审阅的成果正文。"""

    opportunity: Opportunity
    artifacts: list[PreparedArtifact]


def artifact_kind_for(form: ContributionForm) -> ArtifactKind:
    """把贡献形式映射为成果种类（澄清问题归入 reply_draft）。"""
    return _FORM_TO_KIND[form]


def render_evidence_refs(refs: list[EvidenceRef]) -> str:
    """把机会的证据引用渲染进写作 prompt（原文摘录 + 支持判断 + 分层）。"""
    return json.dumps(
        [ref.model_dump(mode="json") for ref in refs],
        ensure_ascii=False,
        indent=2,
    )


def render_voice_summary(profile: VoiceProfile | None) -> str:
    """把 voice profile 压成短摘要；空画像返回 (none)。"""
    if profile is None or profile.is_empty():
        return "(none)"
    lines: list[str] = []
    if profile.preferred_patterns:
        lines.append("偏好: " + "；".join(profile.preferred_patterns[:5]))
    if profile.avoid_phrases:
        lines.append("避免: " + "；".join(profile.avoid_phrases[:5]))
    if profile.rhythm_rules:
        lines.append("节奏: " + "；".join(profile.rhythm_rules[:3]))
    return "\n".join(lines) if lines else "(none)"


def render_user_positions(jobs: list[ContentJob], *, limit: int = 5) -> str:
    """取最近已确认立场（最多 ``limit`` 条）渲染进写作 prompt。"""
    confirmed: list[AuthorPosition] = []
    for job in sorted(jobs, key=lambda j: j.id, reverse=True):
        if job.status != ContentJobStatus.CONFIRMED:
            continue
        if job.author_position is None:
            continue
        confirmed.append(job.author_position)
        if len(confirmed) >= limit:
            break
    if not confirmed:
        return "(none)"
    return json.dumps(
        [
            {
                "claim": p.claim,
                "decision": p.decision,
                "tradeoff": p.tradeoff,
            }
            for p in confirmed
        ],
        ensure_ascii=False,
        indent=2,
    )


def write_contribution(
    runner: StructuredInferenceRunner,
    opportunity: Opportunity,
    *,
    voice_profile: VoiceProfile | None = None,
    confirmed_jobs: list[ContentJob] | None = None,
) -> str:
    """按机会的 proposal 生成贡献正文（纯正文，不落库、不改状态）。"""
    p = opportunity.proposal
    prompt = _PROMPT.read_text().format(
        topic=opportunity.topic or "(none)",
        entry_kind=opportunity.entry_kind.value if opportunity.entry_kind else "none",
        why_me=opportunity.why_me or "(none)",
        why_continue=opportunity.why_continue or "(none)",
        contribution=p.contribution if p else "(none)",
        form=p.form.value if p else "method_card",
        expected_output=p.expected_output if p else "(none)",
        scope=p.scope if p else "(none)",
        cost_note=(p.cost_note if p and p.cost_note else "(none)"),
        evidence=render_evidence_refs(opportunity.evidence_refs),
        voice_summary=render_voice_summary(voice_profile),
        user_positions=render_user_positions(confirmed_jobs or []),
    )
    out = cast(ContributionBodyOutput, runner.run(prompt, ContributionBodyOutput))
    return out.body


def prepare_contribution(
    *,
    opportunity: Opportunity,
    runner: StructuredInferenceRunner,
    service: OpportunityService,
    voice_profile: VoiceProfile | None = None,
    confirmed_jobs: list[ContentJob] | None = None,
) -> PreparedContribution:
    """选定机会后按需制作：生成正文 → 写文件 → 登记 Artifact → mark_ready。

    正文是可审阅表达方案，默认 material_origin=SYNTHETIC、execution_status=NOT_RUN；
    演示是否真正运行由用户后续提供事实更新，代码不得把未运行标为已运行。
    """
    if service.artifacts is None:
        raise ValueError("prepare_contribution requires an ArtifactRepository")
    with service.locked(opportunity.id):
        opp = service.get(opportunity.id)
        if opp is None:
            raise KeyError(opportunity.id)
        # 未选中直接拒绝，避免先调模型/写文件后才因状态转换非法报错。
        if opp.status != OpportunityStatus.SELECTED:
            raise ValueError(
                f"illegal state to prepare: {opp.status.value} "
                f"(expected selected) for opportunity {opportunity.id}"
            )
        body = write_contribution(
            runner,
            opp,
            voice_profile=voice_profile,
            confirmed_jobs=confirmed_jobs,
        )
        p = opp.proposal
        form = p.form if p else ContributionForm.METHOD_CARD
        artifact_id = f"art_{opp.id}_{form.value}"
        source_refs = [
            ref.source_ref for ref in opp.evidence_refs if ref.source_ref
        ]
        service.artifacts.write_content(opp.id, artifact_id, body)
        artifact = Artifact(
            id=artifact_id,
            kind=artifact_kind_for(form),
            path=f"artifacts/{artifact_id}.md",
            source_refs=source_refs,
            material_origin=MaterialOrigin.SYNTHETIC,
            execution_status=ExecutionStatus.NOT_RUN,
        )
        service.add_artifact(opp.id, artifact)
        ready = service.mark_ready(opp.id)
        prepared_artifact = PreparedArtifact(
            id=artifact.id,
            kind=artifact.kind,
            body=body,
            source_refs=source_refs,
            execution_status=artifact.execution_status,
        )
        return PreparedContribution(
            opportunity=ready,
            artifacts=[prepared_artifact],
        )
