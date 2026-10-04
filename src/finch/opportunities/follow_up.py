"""接续建议（规范 §10.4 / §14.3）：用户提供回应后判断是否形成下一步机会。

MVP 不自动抓取回应；由用户主动提供 ``reply_body`` / ``reply_url``。实质回应可生成
关联的新 ``proposed`` 机会（``previous_opportunity_id`` 指向前次）；无价值则只保留
inbound 互动事实。原机会不复活、不保持永久运行。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.engagement.models import (
    FeedbackSnapshot,
    InteractionRecord,
    VerificationStatus,
)
from finch.llm.base import StructuredInferenceRunner
from finch.opportunities.assess import build_opportunity
from finch.opportunities.models import (
    ContributionForm,
    EntryKind,
    EvidenceRef,
    Fit,
    NextAction,
    Opportunity,
    Problem,
)
from finch.opportunities.service import OpportunityService
from finch.storage.repositories import (
    FeedbackSnapshotRepository,
    InteractionRecordRepository,
)

_PROMPT = Path("prompts/follow-up.md")


class FollowUpDraft(BaseModel):
    """接续判断：是否实质回应 + 是否值得提出下一步最小贡献。"""

    meaningful: bool = False
    recommend: bool = False
    skip_reason: str = ""
    topic: str = ""
    thread_ref: str = ""
    entry_kind: EntryKind | None = None
    why_me: str = ""
    why_continue: str = ""
    contribution: str = ""
    form: ContributionForm = ContributionForm.REPLY_DRAFT
    expected_output: str = ""
    scope: str = ""
    cost_note: str = ""
    open_questions: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    problem: Problem | None = None
    fit: Fit | None = None
    next_action: NextAction | None = None
    eval_failed: bool = False


@dataclass
class FollowUpResult:
    """接续命令结果：inbound 记录 + 可选新机会。"""

    interaction: InteractionRecord
    meaningful: bool
    opportunity: Opportunity | None = None
    skip_reason: str = ""
    reused_interaction: bool = False


def _reply_record_id(*, opportunity_id: str, reply_url: str, reply_body: str) -> str:
    raw = f"{opportunity_id}:{reply_url}:{reply_body}"
    return f"rec_in_{hashlib.sha256(raw.encode()).hexdigest()[:12]}"


def _follow_up_opportunity_id(*, previous_id: str, reply_url: str, reply_body: str) -> str:
    raw = f"{previous_id}:{reply_url}:{reply_body}"
    return f"opp_fu_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def assess_follow_up(
    runner: StructuredInferenceRunner,
    *,
    previous: Opportunity,
    reply_body: str,
    reply_url: str = "",
) -> FollowUpDraft:
    """LLM 判断回应是否实质、是否有下一步贡献；失败 → fail-soft 不推荐。"""
    p = previous.proposal
    prompt = _PROMPT.read_text().format(
        previous_id=previous.id,
        topic=previous.topic or "(none)",
        entry_kind=previous.entry_kind.value if previous.entry_kind else "none",
        why_me=previous.why_me or "(none)",
        why_continue=previous.why_continue or "(none)",
        contribution=p.contribution if p else "(none)",
        form=p.form.value if p else "none",
        expected_output=p.expected_output if p else "(none)",
        reply_url=reply_url or "(none)",
        reply_body=reply_body or "(none)",
    )
    try:
        return cast(FollowUpDraft, runner.run(prompt, FollowUpDraft))
    except Exception:
        return FollowUpDraft(
            meaningful=False,
            recommend=False,
            skip_reason="接续判断失败（LLM 异常）",
            eval_failed=True,
        )


def follow_up_opportunity(
    *,
    previous: Opportunity,
    reply_body: str,
    reply_url: str = "",
    peer_id: str,
    platform: str = "x",
    runner: StructuredInferenceRunner,
    service: OpportunityService,
    interactions: InteractionRecordRepository,
    feedbacks: FeedbackSnapshotRepository | None = None,
) -> FollowUpResult:
    """登记 inbound 回应 → 判断实质与接续 → 可选创建新 proposed 机会。"""
    if not reply_body.strip() and not reply_url.strip():
        raise ValueError("reply_body or reply_url is required")
    record_id = _reply_record_id(
        opportunity_id=previous.id,
        reply_url=reply_url,
        reply_body=reply_body,
    )
    existing = interactions.get(record_id)
    if existing is not None:
        # 幂等：同一回应已登记；若已有后续机会则一并返回。
        next_id = _follow_up_opportunity_id(
            previous_id=previous.id, reply_url=reply_url, reply_body=reply_body
        )
        return FollowUpResult(
            interaction=existing,
            meaningful=bool(existing.outcome == "meaningful"),
            opportunity=service.get(next_id),
            skip_reason="已登记过该回应",
            reused_interaction=True,
        )

    now = datetime.now(UTC)
    record = InteractionRecord(
        id=record_id,
        opportunity_id=previous.id,
        peer_id=peer_id,
        platform=platform,
        source_url=reply_url or f"manual:{previous.id}:{record_id}",
        published_body=reply_body,
        body=reply_body,
        occurred_at=now,
        observed_at=now,
        direction="inbound",
        verification_status=VerificationStatus.USER_ATTESTED,
        provenance="connections.follow-up",
        platform_message_id=reply_url or None,
    )
    interactions.upsert(record)

    draft = assess_follow_up(
        runner,
        previous=previous,
        reply_body=reply_body,
        reply_url=reply_url,
    )
    if draft.meaningful:
        record = record.model_copy(update={"outcome": "meaningful"})
        interactions.upsert(record)
        if feedbacks is not None:
            feedbacks.upsert(
                FeedbackSnapshot(
                    id=f"fs_{record.id}",
                    interaction_id=record.id,
                    meaningful=True,
                    captured_at=now,
                )
            )

    next_opp: Opportunity | None = None
    skip_reason = draft.skip_reason
    if draft.meaningful and draft.recommend and draft.contribution.strip():
        # Reuse build_opportunity by adapting FollowUpDraft fields.
        from finch.opportunities.assess import OpportunityDraft

        adapted = OpportunityDraft(
            topic=draft.topic or previous.topic,
            thread_ref=draft.thread_ref or previous.thread_ref or reply_url,
            entry_kind=draft.entry_kind or previous.entry_kind,
            why_me=draft.why_me,
            why_continue=draft.why_continue,
            contribution=draft.contribution,
            form=draft.form,
            expected_output=draft.expected_output,
            scope=draft.scope,
            cost_note=draft.cost_note,
            open_questions=list(draft.open_questions),
            evidence_refs=list(draft.evidence_refs),
            problem=draft.problem,
            fit=draft.fit,
            next_action=draft.next_action,
            recommend=True,
        )
        next_id = _follow_up_opportunity_id(
            previous_id=previous.id, reply_url=reply_url, reply_body=reply_body
        )
        built = build_opportunity(
            adapted,
            opportunity_id=next_id,
            person_ref=previous.person_ref,
            thread_ref=draft.thread_ref or previous.thread_ref,
        )
        if built is not None:
            built = built.model_copy(
                update={"previous_opportunity_id": previous.id}
            )
            next_opp = service.create_from(built)
    elif not draft.meaningful:
        skip_reason = skip_reason or "回应非实质（礼貌/点赞不计）"
    elif not draft.recommend:
        skip_reason = skip_reason or "暂无接续建议"

    return FollowUpResult(
        interaction=record,
        meaningful=draft.meaningful,
        opportunity=next_opp,
        skip_reason=skip_reason,
    )
