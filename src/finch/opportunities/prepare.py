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
from finch.expression_methods.models import ExpressionMethod
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
    Reaction,
)
from finch.opportunities.service import OpportunityService
from finch.profile.models import PracticeProfile
from finch.profile.render import render_user_practices

_PROMPT = Path("prompts/prepare-contribution.md")

_FORM_TO_KIND: dict[ContributionForm, ArtifactKind] = {
    ContributionForm.REPLY_DRAFT: ArtifactKind.REPLY_DRAFT,
    ContributionForm.METHOD_CARD: ArtifactKind.METHOD_CARD,
    ContributionForm.DEMO: ArtifactKind.DEMO,
    ContributionForm.CASE: ArtifactKind.CASE,
    ContributionForm.CLARIFYING_QUESTION: ArtifactKind.REPLY_DRAFT,
}


class ContributionBodyOutput(BaseModel):
    """贡献正文输出：body + 可选方法选择元数据（单次推理产出，不逐方法调模型）。"""

    body: str
    method_id: str | None = None
    fit_reason: str = ""
    response_focus: str = ""


@dataclass
class PreparedArtifact:
    """可审阅成果：正文 + 来源 + 执行状态 + 方法元数据（与 Artifact 一一对应）。"""

    id: str
    kind: ArtifactKind
    body: str
    source_refs: list[str]
    execution_status: ExecutionStatus
    method_ref: str | None = None
    method_version_hash: str = ""
    response_focus: str = ""
    fit_reason: str = ""
    style_policy_version: str = ""


@dataclass
class PreparedContribution:
    """``prepare_contribution`` 的结果：机会（ready）+ 可直接审阅的成果正文。

    ``reaction`` 是正文所依据的最新一条用户反应（无则 None）；``form_forced`` 为 True 表示
    因无反应被代码强制为 clarifying_question。
    """

    opportunity: Opportunity
    artifacts: list[PreparedArtifact]
    reaction: Reaction | None = None
    form_forced: bool = False


def artifact_kind_for(form: ContributionForm) -> ArtifactKind:
    """把贡献形式映射为成果种类（澄清问题归入 reply_draft）。"""
    return _FORM_TO_KIND[form]


def effective_form(opportunity: Opportunity) -> ContributionForm:
    """本设计唯一的 Python 门禁：没有任何用户反应 → 只能准备澄清问题。

    有反应 → 沿用 assess 给出的 ``proposal.form``，如何用进原话由写作 prompt 决定。
    """
    p = opportunity.proposal
    base = p.form if p else ContributionForm.METHOD_CARD
    return base if opportunity.reactions else ContributionForm.CLARIFYING_QUESTION


def artifact_id_for(opportunity: Opportunity, form: ContributionForm) -> str:
    """成果 id：无反应沿用 ``art_{opp}_{form}``（不动现有数据）；有反应加 ``_r{seq}``。"""
    base = f"art_{opportunity.id}_{form.value}"
    if opportunity.reactions:
        return f"{base}_r{opportunity.reactions[-1].seq}"
    return base


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


REPLY_STYLE_POLICY_VERSION = "1.0.0"


def render_reply_style() -> str:
    """回复场景默认简洁策略（代码持有、确定性；版本见 REPLY_STYLE_POLICY_VERSION）。

    这是「回复场景默认策略」一层，优先级低于本次用户指令与 VoiceProfile（见写作 prompt）。
    只约束 reply_draft / clarifying_question 这类回复形正文，方法卡等长形式不受长度目标约束。
    """
    return (
        "一个重点，一条回复，通常 1–3 句；中文目标 40–100 字，软上限 120 字。\n"
        "直接说最有价值的判断或问题，少铺垫（少用「我理解的转折点是」这类起手）。\n"
        "用具体条件、例子、取舍表达观点，避免抽象术语层层展开。\n"
        "语气平实，有判断但不替对方下结论；不重复原帖完整观点，不附长篇总结。\n"
        "不默认赞美开头；不用「深刻」「本质上」「真正该问」等提高姿态的措辞。\n"
        "问题优先问反常做法的成因与保留依据（你最初为什么试这个改法、什么结果让你保留它），"
        "而非泛泛认同或话题级提问。\n"
        "不强制「不是 X 而是 Y」、反问、三段式、行动号召。\n"
        "问题 0–1 个，且必须有明确的信息需求；不强制以问句结尾，准确补一条信息即可结束。\n"
        "单条信息已完整时允许更短；必要条件不能为凑长度删除。"
    )


def render_reply_methods(methods: list[ExpressionMethod] | None) -> str:
    """把回复候选方法渲染进写作 prompt（只给 id/title/reply_usage/reply_boundaries）。"""
    if not methods:
        return "(none)"
    lines: list[str] = []
    for m in methods:
        lines.append(
            f"- id={m.id} | title={m.title} | reply_usage={m.reply_usage or '(none)'} | "
            f"reply_boundaries={m.reply_boundaries or '(none)'}"
        )
    return "\n".join(lines)


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
    practice_profile: PracticeProfile | None = None,
    user_reaction: str | None = None,
    form: ContributionForm | None = None,
    style_note: str | None = None,
    methods: list[ExpressionMethod] | None = None,
) -> ContributionBodyOutput:
    """按机会的 proposal 生成贡献正文（纯正文，不落库、不改状态）。

    ``practice_profile`` 只渲染 confirmed 条目；None / 空 → ``(none)``，正文维持假设场景写法。
    ``user_reaction`` 是用户对这条机会的原话（None → ``(none)``）；``form`` 覆盖 proposal 的
    形式（由 ``effective_form`` 决定），None 时沿用 proposal。
    ``style_note`` 是本次用户风格指令（如「再短一点」），优先级最高；None → ``(none)``。
    ``methods`` 是回复候选方法（可为空）；模型在一次推理内选中最多 1 个，``method_id``
    必须 ∈ 候选集合（否则代码拒绝，不信任模型）。
    """
    p = opportunity.proposal
    resolved_form = form if form is not None else (p.form if p else ContributionForm.METHOD_CARD)
    reaction_text = user_reaction.strip() if user_reaction and user_reaction.strip() else "(none)"
    style_note_text = style_note.strip() if style_note and style_note.strip() else "(none)"
    prompt = _PROMPT.read_text().format(
        topic=opportunity.topic or "(none)",
        entry_kind=opportunity.entry_kind.value if opportunity.entry_kind else "none",
        why_me=opportunity.why_me or "(none)",
        why_continue=opportunity.why_continue or "(none)",
        contribution=p.contribution if p else "(none)",
        form=resolved_form.value,
        expected_output=p.expected_output if p else "(none)",
        scope=p.scope if p else "(none)",
        cost_note=(p.cost_note if p and p.cost_note else "(none)"),
        evidence=render_evidence_refs(opportunity.evidence_refs),
        voice_summary=render_voice_summary(voice_profile),
        user_positions=render_user_positions(confirmed_jobs or []),
        user_practices=render_user_practices(practice_profile),
        user_reaction=reaction_text,
        reply_style=render_reply_style(),
        style_note=style_note_text,
        methods=render_reply_methods(methods),
    )
    out = cast(ContributionBodyOutput, runner.run(prompt, ContributionBodyOutput))
    candidate_ids = {m.id for m in (methods or [])}
    if out.method_id is not None and out.method_id not in candidate_ids:
        raise ValueError(f"method_id not in candidates: {out.method_id}")
    return out


def prepare_contribution(
    *,
    opportunity: Opportunity,
    runner: StructuredInferenceRunner,
    service: OpportunityService,
    voice_profile: VoiceProfile | None = None,
    confirmed_jobs: list[ContentJob] | None = None,
    practice_profile: PracticeProfile | None = None,
    reaction: str | None = None,
    style_note: str | None = None,
    methods: list[ExpressionMethod] | None = None,
) -> PreparedContribution:
    """选定机会后按需制作：记录反应 → 决定形式 → 生成正文 → 写文件 → 登记 Artifact → mark_ready。

    ``reaction`` 非 None 时先经 ``record_reaction`` 落库（空白拒绝、同文本幂等）。没有任何
    反应 → 形式强制 clarifying_question（唯一的 Python 门禁）。正文是可审阅表达方案，默认
    material_origin=SYNTHETIC、execution_status=NOT_RUN；代码不得把未运行标为已运行。
    ``style_note`` 是本次用户风格指令，透传给 ``write_contribution``（None → 用默认策略）。
    ``methods`` 是回复候选方法（可为空）；选中方法与方法版本 hash 落进 Artifact 元数据。
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
        if reaction is not None:
            opp = service.record_reaction(opp.id, text=reaction)
        form = effective_form(opp)
        latest = opp.reactions[-1] if opp.reactions else None
        out = write_contribution(
            runner,
            opp,
            voice_profile=voice_profile,
            confirmed_jobs=confirmed_jobs,
            practice_profile=practice_profile,
            user_reaction=latest.text if latest else None,
            form=form,
            style_note=style_note,
            methods=methods,
        )
        method_version_hash = ""
        if out.method_id is not None:
            for m in methods or []:
                if m.id == out.method_id:
                    method_version_hash = m.content_fingerprint()
                    break
        artifact_id = artifact_id_for(opp, form)
        source_refs = [
            ref.source_ref for ref in opp.evidence_refs if ref.source_ref
        ]
        service.artifacts.write_content(opp.id, artifact_id, out.body)
        artifact = Artifact(
            id=artifact_id,
            kind=artifact_kind_for(form),
            path=f"artifacts/{artifact_id}.md",
            source_refs=source_refs,
            material_origin=MaterialOrigin.SYNTHETIC,
            execution_status=ExecutionStatus.NOT_RUN,
            method_ref=out.method_id,
            method_version_hash=method_version_hash,
            response_focus=out.response_focus,
            fit_reason=out.fit_reason,
            style_policy_version=REPLY_STYLE_POLICY_VERSION,
        )
        service.add_artifact(opp.id, artifact)
        ready = service.mark_ready(opp.id)
        prepared_artifact = PreparedArtifact(
            id=artifact.id,
            kind=artifact.kind,
            body=out.body,
            source_refs=source_refs,
            execution_status=artifact.execution_status,
            method_ref=artifact.method_ref,
            method_version_hash=artifact.method_version_hash,
            response_focus=artifact.response_focus,
            fit_reason=artifact.fit_reason,
            style_policy_version=artifact.style_policy_version,
        )
        return PreparedContribution(
            opportunity=ready,
            artifacts=[prepared_artifact],
            reaction=latest,
            form_forced=latest is None,
        )
