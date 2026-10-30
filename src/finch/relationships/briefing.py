"""周度交流简报：LLM 评估排序/切入点 + 确定性门禁（证据子集、防编造、上限、幂等缓存）。

原则：简报只给「一个人 + 一个切入点 + 依据 + 未知项」；``basis_refs`` 必须是该人物的
``source_refs`` ∪ 事件 quotes ∪ 实际交流 source_refs 的子集，空 refs 强制清空
``contribution`` 并标记「未验证」，不编造「我也遇到过」。LLM 不写任何总分。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.relationships.ids import briefing_id_for
from finch.relationships.models import (
    Briefing,
    BriefingChannel,
    BriefingEntry,
    Contact,
    NextStep,
    PersonProfile,
    TaskEvent,
)
from finch.relationships.repository import BriefingRepository

_PROMPT = Path("prompts/relationship-briefing.md")
BRIEFING_VERSION = "relationship-briefing-v1"


class BriefingEntryDraft(BaseModel):
    """LLM 输出的单条简报（enum 用 str 宽容解析，代码再强制纠正）。"""

    person_id: str = ""
    entry_point: str = ""
    why_now: str = ""
    contribution: str = ""
    question: str = ""
    channel: str = "observe"
    facts_vs_guesses: str = ""
    basis_refs: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)


class BriefingDraft(BaseModel):
    """一次 LLM 简报调用返回：按相关性排序的条目（LLM 排序，不写总分）。"""

    entries: list[BriefingEntryDraft] = Field(default_factory=list)
    eval_failed: bool = False
    skip_reason: str = ""


def _fingerprint(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _coerce_channel(value: str) -> BriefingChannel:
    try:
        return BriefingChannel(value)
    except ValueError:
        return BriefingChannel.OBSERVE


def _group[T](items: list[T], key) -> dict[str, list[T]]:
    out: dict[str, list[T]] = {}
    for it in items:
        out.setdefault(key(it), []).append(it)
    return out


def render_candidates_json(
    people: list[PersonProfile],
    events: list[TaskEvent],
    contacts: list[Contact],
    next_steps: list[NextStep],
) -> str:
    """把人物 + 其事件/实际交流/下一步压缩成 prompt 可消费的 JSON（草稿交流不进入）。"""
    events_by = _group(events, key=lambda e: e.person_id)
    actual_contacts = [c for c in contacts if not c.is_draft]
    contacts_by = _group(actual_contacts, key=lambda c: c.person_id)
    steps_by = _group(next_steps, key=lambda n: n.person_id)
    rows = []
    for p in people:
        rows.append(
            {
                "person_id": p.person_id,
                "display_name": p.display_name,
                "role": p.role,
                "current_focus": p.current_focus,
                "source_refs": p.source_refs,
                "events": [
                    {
                        "task": e.task,
                        "expectation": e.expectation,
                        "trust_boundary": e.trust_boundary,
                        "problem": e.problem,
                        "workaround": e.workaround,
                        "cognitive_change": e.cognitive_change,
                        "source_refs": e.source_refs,
                    }
                    for e in events_by.get(p.person_id, [])
                ],
                "contacts": [
                    {
                        "channel": c.channel,
                        "summary": c.summary,
                        "feedback": c.feedback,
                        "commitments": [
                            {
                                "text": cm.text,
                                "due_at": cm.due_at.isoformat() if cm.due_at else "",
                                "status": cm.status.value,
                            }
                            for cm in c.commitments
                        ],
                        "source_refs": c.source_refs,
                    }
                    for c in contacts_by.get(p.person_id, [])
                ],
                "next_steps": [
                    {"suggestion": n.suggestion, "trigger": n.trigger, "status": n.status.value}
                    for n in steps_by.get(p.person_id, [])
                ],
            }
        )
    return json.dumps(rows, ensure_ascii=False, indent=2)


def assess_briefing(runner: StructuredInferenceRunner, candidates_json: str) -> BriefingDraft:
    """LLM 产出排序后的简报条目；失败 → eval_failed（fail-soft）。"""
    prompt = _PROMPT.read_text().replace("{candidates_json}", candidates_json)
    try:
        return cast(BriefingDraft, runner.run(prompt, BriefingDraft))
    except Exception:
        return BriefingDraft(eval_failed=True, skip_reason="简报生成失败（LLM 异常）")


def _allowed_refs(
    person_id: str,
    persons: list[PersonProfile],
    events: list[TaskEvent],
    contacts: list[Contact],
) -> set[str]:
    refs: set[str] = set()
    for p in persons:
        if p.person_id == person_id:
            refs.update(p.source_refs)
    for e in events:
        if e.person_id == person_id:
            refs.update(e.source_refs)
            refs.update(q.source_ref for q in e.quotes if q.source_ref)
    for c in contacts:
        if c.person_id == person_id:
            refs.update(c.source_refs)
    return refs


def build_briefing(
    draft: BriefingDraft,
    *,
    persons: list[PersonProfile],
    events: list[TaskEvent],
    contacts: list[Contact],
    next_steps: list[NextStep],
    fingerprint: str,
    limit: int,
    now: datetime,
) -> Briefing:
    """确定性把 draft 转为简报：未知人物/空切入点丢弃、refs 子集、无 refs 清空 contribution。"""
    if draft.eval_failed:
        return Briefing(
            briefing_id=briefing_id_for(fingerprint, now.isoformat()),
            fingerprint=fingerprint,
            insufficient=True,
            info_gaps=draft.skip_reason or "简报生成失败",
        )

    person_by_id = {p.person_id: p for p in persons}
    entries: list[BriefingEntry] = []
    for d in draft.entries:
        if d.person_id not in person_by_id:
            continue
        if not d.entry_point.strip():
            continue
        allowed = _allowed_refs(d.person_id, persons, events, contacts)
        refs = [r for r in d.basis_refs if r in allowed]
        contribution = d.contribution.strip()
        unknowns = list(d.unknowns)
        if not refs:
            contribution = ""
            marker = "未验证：无证据依据"
            if marker not in unknowns:
                unknowns.append(marker)
        entries.append(
            BriefingEntry(
                person_id=d.person_id,
                entry_point=d.entry_point.strip(),
                why_now=d.why_now.strip(),
                contribution=contribution,
                question=d.question.strip(),
                channel=_coerce_channel(d.channel),
                facts_vs_guesses=d.facts_vs_guesses.strip(),
                basis_refs=refs,
                unknowns=unknowns,
            )
        )

    return Briefing(
        briefing_id=briefing_id_for(fingerprint, now.isoformat()),
        created_at=now,
        fingerprint=fingerprint,
        entries=entries[:limit],
        insufficient=not entries,
    )


def generate_briefing(
    runner: StructuredInferenceRunner | None,
    *,
    people: list[PersonProfile],
    events: list[TaskEvent],
    contacts: list[Contact],
    next_steps: list[NextStep],
    repository: BriefingRepository,
    version: str = BRIEFING_VERSION,
    limit: int = 3,
    force: bool = False,
    now: datetime | None = None,
) -> Briefing | None:
    """生成周度简报；幂等（同输入指纹复用缓存），``force`` 重算；无人物/无 runner → None。"""
    if not people:
        return None
    fingerprint = _fingerprint(
        {
            "people": sorted(p.person_id for p in people),
            "events": sorted(e.event_id for e in events),
            "contacts": sorted(c.contact_id for c in contacts),
            "next_steps": sorted(n.next_id for n in next_steps),
            "version": version,
            "limit": limit,
        }
    )
    clock = now or datetime.now(UTC)
    if not force:
        existing = repository.latest()
        if existing is not None and existing.fingerprint == fingerprint:
            return existing
    if runner is None:
        return None
    draft = assess_briefing(runner, render_candidates_json(people, events, contacts, next_steps))
    briefing = build_briefing(
        draft,
        persons=people,
        events=events,
        contacts=contacts,
        next_steps=next_steps,
        fingerprint=fingerprint,
        limit=limit,
        now=clock,
    )
    repository.upsert(briefing)
    return briefing
