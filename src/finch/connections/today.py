"""今日行动投影：从关系事实计算最多 N 条「可处理、有理由、可停止」的行动。

纯函数、无 IO、无 LLM。总量与排序由代码确定；``connections today`` 与
``people shortlist --today`` 分属不同语义（后者仍是线索级承诺面），本投影供
``connections today`` 使用。
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from finch.conversations.models import (
    CommitmentStatus,
    ConversationThread,
    FollowUpTrigger,
)
from finch.conversations.service import (
    due_for_review,
    first_contact_review_state,
    has_contact_reason,
)
from finch.engagement.models import InteractionRecord
from finch.peers.models import PeerProfile

_URGENCY_ORDER = {"contact": 0, "attention": 1, "review": 2}


class TodayAction(BaseModel):
    """一条今日行动：同人多线程合并，保留具体线程入口。"""

    person_id: str
    peer_id: str
    thread_ids: list[str]
    primary_thread_id: str
    display_name: str
    topic: str
    last_event: str
    reason: str
    minimal_action: str
    source_time: datetime
    estimated_minutes: int
    urgency: Literal["attention", "review", "contact"]


def _reason_and_action(
    thread: ConversationThread, *, state: str, contact: bool
) -> tuple[str, str]:
    """按触发来源给出「这次理由」与「最小动作」。"""
    if FollowUpTrigger.NEW_REPLY in thread.pending_triggers:
        return "新回复待回应", "查看回复并起草后续"
    open_cmts = [c for c in thread.commitments if c.status is CommitmentStatus.OPEN]
    if FollowUpTrigger.OWN_COMMITMENT in thread.pending_triggers or open_cmts:
        return "承诺待兑现", "兑现承诺或更新状态"
    if thread.open_questions or thread.open_question_notes:
        return "未解问题待澄清", "回答或追问"
    if contact:
        return "有联系理由", "查看并决定下一步"
    if state == "attention":
        return "首触复查超时", "看一眼并决定是否准备后续"
    if state == "reviewable":
        return "首触复查可审阅", "决定是否准备后续"
    return "定期回顾到期", "回顾并决定是否联系"


def _latest_record(
    thread: ConversationThread, rec_by_id: dict[str, InteractionRecord]
) -> InteractionRecord | None:
    recs = [rec_by_id[i] for i in thread.interaction_ids if i in rec_by_id]
    if not recs:
        return None
    return max(recs, key=lambda r: r.occurred_at)


def _last_event(
    thread: ConversationThread,
    latest: InteractionRecord | None,
    state: str,
) -> str:
    if FollowUpTrigger.NEW_REPLY in thread.pending_triggers:
        return "收到新回复"
    if latest is not None and (latest.published_body or latest.body):
        body = (latest.published_body or latest.body).strip()
        return body[:40] + ("…" if len(body) > 40 else "")
    if state in {"attention", "reviewable"}:
        return "首触待复查"
    return "（无正文）"


def build_today_actions(
    *,
    threads: list[ConversationThread],
    interactions: list[InteractionRecord],
    peers: list[PeerProfile],
    now: datetime,
    review_after_hours: int,
    review_due_hours: int,
    budget_minutes: int = 15,
    limit: int = 3,
) -> list[TodayAction]:
    """从关系事实计算今日行动，按 person 去重、优先级排序、上限 ``limit``。"""
    peer_by_id = {p.id: p for p in peers}
    rec_by_id = {r.id: r for r in interactions}

    grouped: dict[str, list[TodayAction]] = {}
    for t in threads:
        contact = has_contact_reason(t, now=now)
        due = due_for_review(t, now=now)
        if not (contact or due):
            continue
        state = first_contact_review_state(
            t, now=now, review_after_hours=review_after_hours, review_due_hours=review_due_hours
        )
        if contact:
            urgency = "contact"
        elif state == "attention":
            urgency = "attention"
        else:
            urgency = "review"
        reason, action = _reason_and_action(t, state=state, contact=contact)
        peer = peer_by_id.get(t.peer_id)
        display = peer.display_name if peer and peer.display_name else t.peer_id
        person_id = peer.person_id if peer and peer.person_id else t.peer_id
        latest = _latest_record(t, rec_by_id)
        source = (
            t.last_activity_at
            or (latest.occurred_at if latest else None)
            or t.follow_up_review_at
            or now
        )
        action_obj = TodayAction(
            person_id=person_id,
            peer_id=t.peer_id,
            thread_ids=[t.id],
            primary_thread_id=t.id,
            display_name=display,
            topic=t.topic,
            last_event=_last_event(t, latest, state),
            reason=reason,
            minimal_action=action,
            source_time=source,
            estimated_minutes=0,
            urgency=urgency,  # type: ignore[arg-type]
        )
        grouped.setdefault(person_id, []).append(action_obj)

    merged: list[TodayAction] = []
    for _person_id, actions in grouped.items():
        thread_ids: list[str] = []
        for a in actions:
            for tid in a.thread_ids:
                if tid not in thread_ids:
                    thread_ids.append(tid)
        primary = min(
            actions,
            key=lambda a: (
                _URGENCY_ORDER[a.urgency],
                -a.source_time.timestamp(),
                a.primary_thread_id,
            ),
        )
        merged.append(primary.model_copy(update={"thread_ids": thread_ids}))

    merged.sort(
        key=lambda a: (
            _URGENCY_ORDER[a.urgency],
            -a.source_time.timestamp(),
            a.primary_thread_id,
        )
    )
    selected = merged[:limit]
    per = math.ceil(budget_minutes / len(selected)) if selected else 0
    for a in selected:
        a.estimated_minutes = per
    return selected


def render_today_actions(actions: list[TodayAction]) -> str:
    """渲染今日行动：每条一行，六段。"""
    if not actions:
        return "no actions today"
    lines = [f"today actions ({len(actions)})"]
    for a in actions:
        when = a.source_time.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"- {a.display_name} → {a.last_event} → {a.reason} → "
            f"{a.minimal_action} → {when} → ~{a.estimated_minutes}min"
        )
    return "\n".join(lines)
