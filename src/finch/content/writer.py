"""Writer：从 ContentJob 语境写原创草稿 + 定向重写（contract C4）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from finch.codex.runner import CodexRunner
from finch.content.checkers.base import CheckResult
from finch.content.clarity import RULES_VERSION, ClarityEditOutput, parse_clarity_preset
from finch.content.jobs import ContentJob
from finch.content.models import Draft, DraftBodyOutput, draft_kind_for
from finch.content.voice import VoiceProfile, render_voice_context
from finch.evidence.models import EvidenceCard, sanitize_model_confidence

_FROM_JOB_PROMPT_PATH = Path("prompts/draft-from-job.md")
_CLARITY_PROMPT_PATH = Path("prompts/clarity-revise.md")
_CLARITY_RULES_PATH = Path("skills/_shared/asd-ste100-inspired.md")

_REWRITE_PROMPT = """\
You rewrite a draft to address specific critic check failures. Return JSON matching the schema.
Instructions:
- Keep the same id, kind, candidate_id, and language as the Original draft.
- Only use evidence cards listed under Evidence cards, referenced by id.
- Every claim must carry an evidence_card_id and a confidence that the card supports.
- Fix exactly the failures listed under Failed checks. Do NOT restyle, polish, or improve
  the rest of the draft — change only what is needed to resolve the listed failures.

{job_context}## Original draft
{body}

## Failed checks
{rewrite_instructions}

## Evidence cards
{cards}
"""


def _render_cards(cards: list[EvidenceCard]) -> str:
    return json.dumps([card.model_dump(mode="json") for card in cards])


def _sanitize_draft_claims(draft: Draft) -> Draft:
    """模型输出不得自行产出 USER_CONFIRMED：逐条降级为 SUPPORTED（计划 Task 1.2）。

    提取器（extractor）已对事件 claim 做同样降级，但 writer 的原创/重写路径各自
    独立调用 LLM 反序列化为 ``Draft``，必须在此再拦一道，否则模型可直接把 claim 标为
    USER_CONFIRMED（可发布且 Critic 不 hard-fail）。
    """
    return draft.model_copy(
        update={
            "claims": [
                ref.model_copy(
                    update={"confidence": sanitize_model_confidence(ref.confidence)}
                )
                for ref in draft.claims
            ]
        }
    )


def _render_job_context(job: ContentJob | None) -> str:
    """渲染 Content Job 上下文，作为首稿与定向重写的显式约束；无 job 时返回空串。

    reader_problem / core_message / why_now / author_position(claim/decision/tradeoff/
    change_mind_if) 正是 DecisionChecker 等 Critic 检查器校验的对象，缺了它们
    writer 无法在首稿直接完成 job（F2）。
    """
    if job is None:
        return ""
    position = job.author_position
    blocks = [
        "## Content job context",
        f"- reader_problem: {job.reader_problem}",
        f"- core_message: {job.core_message or '(none)'}",
        f"- why_now: {job.why_now or '(none)'}",
        f"- observation: {job.observation or '(none)'}",
        f"- intent: {job.intent}",
        f"- open_question: {job.open_question or '(none)'}",
        "## Author's decision and intent",
    ]
    if position is not None:
        blocks.extend(
            [
                f"- claim: {position.claim}",
                f"- decision: {position.decision}",
                f"- tradeoff: {position.tradeoff}",
                f"- change_mind_if: {position.change_mind_if or '(none)'}",
            ]
        )
    else:
        blocks.append("- (no author position)")

    # Observed facts vs interpretation (for judgment_shift and similar types)
    blocks.append("## Observed facts vs interpretation")
    blocks.append(f"- evidence_status: {job.evidence_status or '(none)'}")
    blocks.append(
        f"- facts: {json.dumps(job.facts, ensure_ascii=False) if job.facts else '(none)'}"
    )
    blocks.append(f"- interpretation: {job.interpretation or '(none)'}")
    blocks.append(f"- limitations (适用边界): {job.limitations or '(none)'}")

    # Position revision history (append-only)
    if job.position_revisions:
        blocks.append("## Position revision history (oldest → newest)")
        for rev in job.position_revisions:
            blocks.append(
                f"- claim: {rev.claim} (reason={rev.change_reason or 'n/a'}; "
                f"scope={rev.scope or 'n/a'}; counterexample={rev.counterexample or 'n/a'})"
            )

    return "\n".join(blocks) + "\n\n"


def _render_failed_checks(failed_checks: list[CheckResult]) -> str:
    """只渲染失败检查器的 issue + rewrite_instructions（定向重写，禁止整体润色）。"""
    blocks: list[str] = []
    for check in failed_checks:
        lines = [f"## {check.checker}"]
        for issue in check.issues:
            lines.append(f"- issue: {issue}")
        for instruction in check.rewrite_instructions:
            lines.append(f"- fix: {instruction}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def write_original_from_job(
    runner: CodexRunner,
    job: ContentJob,
    voice_profile: VoiceProfile | None = None,
) -> Draft:
    """只从 Content Job 语境写原创中文草稿（idea 流：不搜索、不绑定证据卡）。

    idea 候选没有证据卡，故用独立 prompt（``prompts/draft-from-job.md``）只依据 job
    语境（读者问题 / 作者立场 / 核心主张 / scope）写正文，``claims`` 恒为空；作者声音画像
    作为风格参照块传入（不当作事实或指令）。
    """
    prompt = _FROM_JOB_PROMPT_PATH.read_text().format(
        job_context=_render_job_context(job),
        voice_context=render_voice_context(voice_profile),
    )
    out = cast(DraftBodyOutput, runner.run(prompt, DraftBodyOutput))
    return Draft(
        id="",
        kind=draft_kind_for(job.recommended_format),
        candidate_id=None,
        language="zh",
        body=out.body,
        claims=[],
        content_job_id=job.id,
        position_statement=(
            job.author_position.decision if job.author_position else ""
        ),
    )


def rewrite(
    runner: CodexRunner,
    draft: Draft,
    failed_checks: list[CheckResult],
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    """按 Critic 失败检查器指令重写（保留 run_id 等来源字段）。"""
    return _rewrite(runner, draft, _render_failed_checks(failed_checks), cards_by_id, job)


def rewrite_with_instruction(
    runner: CodexRunner,
    draft: Draft,
    instruction: str,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    """按自然语言指令重写，并附带 ASD-STE100-inspired ClarityReview（一次调用）。"""
    preset = parse_clarity_preset(instruction)
    card_ids = {ref.evidence_card_id for ref in draft.claims}
    cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
    prompt = _CLARITY_PROMPT_PATH.read_text().format(
        preset=preset,
        rules=_CLARITY_RULES_PATH.read_text(),
        instruction=instruction,
        job_context=_render_job_context(job),
        body=draft.body,
        cards=_render_cards(cards),
    )
    out = cast(ClarityEditOutput, runner.run(prompt, ClarityEditOutput))
    # preset / rules_version 由代码决定，不信任模型；changes 最多保留 3 条
    review = out.clarity_review.model_copy(
        update={
            "preset": preset,
            "rules_version": RULES_VERSION,
            "changes": list(out.clarity_review.changes[:3]),
        }
    )
    revised = _sanitize_draft_claims(draft.model_copy(update={"body": out.body}))
    return revised.model_copy(update={"clarity_review": review})


def _rewrite(
    runner: CodexRunner,
    draft: Draft,
    instructions: str,
    cards_by_id: dict[str, EvidenceCard],
    job: ContentJob | None = None,
) -> Draft:
    card_ids = {ref.evidence_card_id for ref in draft.claims}
    cards = [cards_by_id[cid] for cid in card_ids if cid in cards_by_id]
    prompt = _REWRITE_PROMPT.format(
        body=draft.body,
        job_context=_render_job_context(job),
        rewrite_instructions=instructions,
        cards=_render_cards(cards),
    )
    out = _sanitize_draft_claims(cast(Draft, runner.run(prompt, Draft)))
    return out.model_copy(
        update={
            "id": draft.id,
            "kind": draft.kind,
            "candidate_id": draft.candidate_id,
            "language": draft.language,
            "content_job_id": draft.content_job_id,
            "position_statement": draft.position_statement,
            "run_id": draft.run_id,
        }
    )
