"""对话线索服务：串联互动、恢复上下文、判断是否需要跟进。

本服务只做确定性状态管理（稳定 id、去重追加、跟进判定）；「提出下一步」的语义判断
由 conversation-follow-up Skill（LLM）负责，本模块不注入 runner。

跟进触发：new_reply / own_commitment / new_evidence / related_update，以及未解问题。
时间陈旧只供内部复查优先级，**单独不会**触发对外联系。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from finch.engagement.models import InteractionRecord, VerificationStatus

from .models import (
    Commitment,
    CommitmentStatus,
    ConversationThread,
    FollowUpTrigger,
    MiniExperiment,
    ObservationKind,
    ThreadNote,
    ThreadStatus,
)


class ConversationServiceError(ValueError):
    """对话线索校验失败（修订冲突、缺来源等）。"""


_POLITE_INTEREST_MARKERS = (
    "有空看看",
    "很有趣",
    "interesting",
    "maybe later",
    "有空再说",
    "之后看看",
)

_USAGE_SUCCESS_MARKERS = (
    "跑通",
    "用起来了",
    "started using",
    "completed",
    "运行成功",
)


def thread_id_for(peer_id: str, topic: str) -> str:
    """由 ``(peer_id, topic)`` 派生稳定 thread id（幂等）。"""
    digest = hashlib.sha256(f"{peer_id}:{topic}".encode()).hexdigest()
    return f"thread_{digest[:12]}"


def record_id_for(
    *,
    platform_message_id: str | None,
    source_url: str,
    peer_id: str,
    occurred_at: datetime,
) -> str:
    """平台消息 ID 优先；URL 后备。不含「现在」时间，重跑稳定。"""
    if platform_message_id:
        key = f"msg:{platform_message_id}"
    elif source_url:
        key = f"url:{source_url}"
    else:
        key = f"peer:{peer_id}:{occurred_at.isoformat()}"
    return f"rec_{hashlib.sha256(key.encode()).hexdigest()[:16]}"


def note_id_for(source_ref: str, kind: ObservationKind, text: str) -> str:
    """由 source_ref + kind + 文本指纹派生稳定笔记 id。"""
    digest = hashlib.sha256(
        f"{source_ref}|{kind.value}|{text.strip()}".encode()
    ).hexdigest()
    return f"note_{digest[:12]}"


def commitment_id_for(source_ref: str, text: str) -> str:
    digest = hashlib.sha256(f"{source_ref}|{text.strip()}".encode()).hexdigest()
    return f"cmt_{digest[:12]}"


def refuse_polite_interest_as_usage_success(kind: ObservationKind, text: str) -> None:
    """礼貌兴趣不得记为 usage_feedback 成功类陈述。"""
    if kind is not ObservationKind.USAGE_FEEDBACK:
        return
    blob = text.casefold()
    if any(m.casefold() in blob for m in _POLITE_INTEREST_MARKERS):
        if not any(m.casefold() in blob for m in _USAGE_SUCCESS_MARKERS):
            raise ConversationServiceError(
                "polite interest cannot be recorded as usage_feedback success"
            )


def active_observation_notes(thread: ConversationThread) -> list[ThreadNote]:
    """未 superseded 的 observation 笔记。"""
    return [n for n in thread.observation_notes if not n.superseded]


class ConversationService:
    """对话线索的确定性操作。"""

    def open_thread(
        self,
        *,
        peer_id: str,
        topic: str,
        root_message_id: str | None = None,
    ) -> ConversationThread:
        """为 (peer, topic) 打开一条对话线索（幂等：同一对恒同 id）。"""
        return ConversationThread(
            id=thread_id_for(peer_id, topic),
            peer_id=peer_id,
            topic=topic,
            root_message_id=root_message_id,
        )

    def append_interaction(
        self, thread: ConversationThread, interaction_id: str, *, occurred_at: datetime
    ) -> ConversationThread:
        """把一次互动串进线索；同 interaction_id 幂等（不重复），并刷新最近活动时间。"""
        updated = thread.model_copy(deep=True)
        if interaction_id not in updated.interaction_ids:
            updated.interaction_ids.append(interaction_id)
        if updated.last_activity_at is None or occurred_at > updated.last_activity_at:
            updated.last_activity_at = occurred_at
        return updated

    def set_status(self, thread: ConversationThread, status: ThreadStatus) -> ConversationThread:
        """变更线索状态（ACTIVE/DORMANT/CLOSED/DEFERRED），返回新对象。"""
        return thread.model_copy(update={"status": status})

    def defer(
        self, thread: ConversationThread, *, until: datetime
    ) -> ConversationThread:
        return thread.model_copy(
            update={"status": ThreadStatus.DEFERRED, "defer_until": until}
        )

    def add_trigger(
        self, thread: ConversationThread, trigger: FollowUpTrigger
    ) -> ConversationThread:
        updated = thread.model_copy(deep=True)
        if trigger not in updated.pending_triggers:
            updated.pending_triggers.append(trigger)
        return updated

    def clear_triggers(self, thread: ConversationThread) -> ConversationThread:
        return thread.model_copy(update={"pending_triggers": []})

    def add_commitment(
        self,
        thread: ConversationThread,
        commitment: Commitment,
        *,
        expected_revision: int | None = None,
    ) -> ConversationThread:
        if expected_revision is not None and expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        updated = thread.model_copy(deep=True)
        if any(c.id == commitment.id for c in updated.commitments):
            return updated
        updated.commitments.append(commitment)
        if expected_revision is not None:
            updated.revision = thread.revision + 1
        # Only queue OWN_COMMITMENT when due now / undated; future due waits for due_at.
        from datetime import UTC

        now = datetime.now(UTC)
        if commitment.due_at is None or commitment.due_at <= now:
            return self.add_trigger(updated, FollowUpTrigger.OWN_COMMITMENT)
        return updated

    def build_observation_note(
        self,
        *,
        kind: ObservationKind | str,
        text: str,
        source_ref: str,
        tool_ref: str | None = None,
        supersedes_id: str | None = None,
    ) -> ThreadNote:
        if not text or not text.strip():
            raise ConversationServiceError("note text is required")
        if not source_ref or not source_ref.strip():
            raise ConversationServiceError("source_ref is required")
        obs_kind = ObservationKind(kind) if isinstance(kind, str) else kind
        refuse_polite_interest_as_usage_success(obs_kind, text)
        return ThreadNote(
            id=note_id_for(source_ref.strip(), obs_kind, text),
            text=text.strip(),
            source_ref=source_ref.strip(),
            kind=obs_kind,
            tool_ref=tool_ref,
            supersedes_id=supersedes_id,
        )

    def upsert_observation_note(
        self,
        thread: ConversationThread,
        note: ThreadNote,
        *,
        expected_revision: int,
        known_interaction_ids: set[str] | None = None,
    ) -> ConversationThread:
        """幂等写入 observation 笔记；修订冲突明确报错。"""
        if expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        if note.kind is None:
            raise ConversationServiceError("observation note kind is required")
        if not note.source_ref:
            raise ConversationServiceError("source_ref is required")
        if known_interaction_ids is not None and note.source_ref not in known_interaction_ids:
            # Allow URL-style refs only when they match an interaction id already on thread
            # or the explicit known set; otherwise refuse dangling refs.
            if note.source_ref not in thread.interaction_ids:
                raise ConversationServiceError(
                    f"source_ref not found: {note.source_ref}"
                )

        refuse_polite_interest_as_usage_success(note.kind, note.text)
        updated = thread.model_copy(deep=True)

        # Idempotent: same id already active → no-op (no revision bump).
        for existing in updated.observation_notes:
            if existing.id == note.id and not existing.superseded:
                return thread

        # Same source_ref+kind with different text: require supersedes of prior active.
        same_slot = [
            n
            for n in updated.observation_notes
            if not n.superseded
            and n.kind == note.kind
            and n.source_ref == note.source_ref
        ]
        if same_slot and not note.supersedes_id:
            # Treat identical text as already covered by id check; different text needs
            # explicit supersede.
            prior = same_slot[-1]
            if prior.text.strip() == note.text.strip():
                return thread
            raise ConversationServiceError(
                "correcting an observation requires supersedes_id"
            )

        if note.supersedes_id:
            found = False
            for i, existing in enumerate(updated.observation_notes):
                if existing.id == note.supersedes_id:
                    updated.observation_notes[i] = existing.model_copy(
                        update={"superseded": True}
                    )
                    found = True
            if not found:
                raise ConversationServiceError(
                    f"supersedes target not found: {note.supersedes_id}"
                )

        updated.observation_notes.append(note)
        updated.revision = thread.revision + 1
        return updated

    def needs_follow_up(
        self, thread: ConversationThread, *, now: datetime, stale_days: int = 7
    ) -> bool:
        """是否需要跟进（可对外呈现）。

        CLOSED 不跟进；DEFERRED 且未到期不跟进。
        触发：pending_triggers、到期开放承诺、未解问题、到期重要关系回顾。
        未来到期的承诺 alone 不触发；``stale_days`` 保留签名兼容，但**单独过期不触发**对外联系。
        """
        del stale_days  # time alone must not contact the other person
        if thread.status is ThreadStatus.CLOSED:
            return False
        if thread.status is ThreadStatus.DEFERRED:
            if thread.defer_until is not None and thread.defer_until > now:
                return False
        if thread.pending_triggers:
            return True
        for c in thread.commitments:
            if c.status is not CommitmentStatus.OPEN:
                continue
            # Open commitment with no due_at still needs follow-up (explicit promise).
            # Future due_at alone does not fire until due.
            if c.due_at is None or c.due_at <= now:
                return True
        if thread.open_questions or thread.open_question_notes:
            return True
        if (
            thread.important_review_enabled
            and thread.next_review_at is not None
            and thread.next_review_at <= now
        ):
            return True
        return False

    def is_stale_for_internal_review(
        self, thread: ConversationThread, *, now: datetime, stale_days: int = 7
    ) -> bool:
        """内部复查用：时间陈旧，不意味着应联系对方。"""
        if thread.status is ThreadStatus.CLOSED:
            return False
        if thread.last_activity_at is None:
            return True
        return (now - thread.last_activity_at).days >= stale_days

    def add_experiment(
        self,
        thread: ConversationThread,
        experiment: MiniExperiment,
        *,
        expected_revision: int | None = None,
        source_ref: str = "",
    ) -> ConversationThread:
        """Adopt a MiniExperiment (explicit only). Suggestions never auto-create."""
        if expected_revision is not None and expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        updated = thread.model_copy(deep=True)
        # Idempotent on hypothesis + method (+ optional source in artifact_ref).
        key = (
            experiment.hypothesis.strip().casefold(),
            (experiment.method or "").strip().casefold(),
            (source_ref or experiment.artifact_ref or "").strip().casefold(),
        )
        for existing in updated.experiments:
            existing_key = (
                existing.hypothesis.strip().casefold(),
                (existing.method or "").strip().casefold(),
                (existing.artifact_ref or "").strip().casefold(),
            )
            if existing_key == key:
                return updated
        to_add = experiment
        if source_ref and not experiment.artifact_ref:
            to_add = experiment.model_copy(update={"artifact_ref": source_ref})
        updated.experiments.append(to_add)
        if expected_revision is not None:
            updated.revision = thread.revision + 1
        return updated

    def record_experiment_result(
        self,
        thread: ConversationThread,
        *,
        hypothesis: str,
        actual_result: str,
        artifact_ref: str | None = None,
        expected_revision: int | None = None,
    ) -> ConversationThread:
        """Record result on matching experiment; appends NEW_EVIDENCE trigger."""
        if expected_revision is not None and expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        if not actual_result.strip():
            raise ConversationServiceError("actual_result is required")
        updated = thread.model_copy(deep=True)
        target = hypothesis.strip().casefold()
        found = False
        for i, exp in enumerate(updated.experiments):
            if exp.hypothesis.strip().casefold() == target:
                updates: dict = {"actual_result": actual_result.strip()}
                if artifact_ref:
                    updates["artifact_ref"] = artifact_ref
                updated.experiments[i] = exp.model_copy(update=updates)
                found = True
                break
        if not found:
            raise ConversationServiceError(f"experiment not found: {hypothesis}")
        if expected_revision is not None:
            updated.revision = thread.revision + 1
        return self.add_trigger(updated, FollowUpTrigger.NEW_EVIDENCE)

    def mark_important(
        self,
        thread: ConversationThread,
        *,
        cadence_days: int,
        now: datetime,
        expected_revision: int | None = None,
    ) -> ConversationThread:
        """Opt-in important-relationship periodic review (remind only, no auto-greet)."""
        if cadence_days < 1:
            raise ConversationServiceError("cadence_days must be >= 1")
        if expected_revision is not None and expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        from datetime import timedelta

        next_at = now + timedelta(days=cadence_days)
        updates = {
            "important_review_enabled": True,
            "review_cadence_days": cadence_days,
            "next_review_at": next_at,
        }
        if expected_revision is not None:
            updates["revision"] = thread.revision + 1
        return thread.model_copy(update=updates)

    def clear_important_review(
        self,
        thread: ConversationThread,
        *,
        expected_revision: int | None = None,
    ) -> ConversationThread:
        if expected_revision is not None and expected_revision != thread.revision:
            raise ConversationServiceError(
                f"revision conflict: expected {expected_revision}, "
                f"current {thread.revision}"
            )
        updates: dict = {
            "important_review_enabled": False,
            "review_cadence_days": None,
            "next_review_at": None,
        }
        if expected_revision is not None:
            updates["revision"] = thread.revision + 1
        return thread.model_copy(update=updates)

    def ingest_record(
        self,
        existing: InteractionRecord | None,
        *,
        peer_id: str,
        platform: str,
        source_url: str,
        body: str,
        occurred_at: datetime,
        platform_message_id: str | None = None,
        direction: str = "unknown",
        verification_status: VerificationStatus = VerificationStatus.UNVERIFIED,
        proposal_id: str | None = None,
        observed_at: datetime | None = None,
    ) -> InteractionRecord:
        """幂等写入互动事实：已存在则保留原 ``occurred_at``，不漂移。"""
        rid = record_id_for(
            platform_message_id=platform_message_id,
            source_url=source_url,
            peer_id=peer_id,
            occurred_at=occurred_at,
        )
        if existing is not None and existing.id == rid:
            # Rerun: keep original occurred_at; may refresh observed_at / body if empty.
            updates: dict = {}
            if observed_at is not None:
                updates["observed_at"] = observed_at
            if body and not existing.published_body and not existing.body:
                updates["published_body"] = body
                updates["body"] = body
            return existing.model_copy(update=updates) if updates else existing
        return InteractionRecord(
            id=rid,
            proposal_id=proposal_id,
            peer_id=peer_id,
            platform=platform,
            source_url=source_url,
            published_body=body,
            body=body,
            occurred_at=occurred_at,
            observed_at=observed_at or occurred_at,
            platform_message_id=platform_message_id,
            direction=direction,  # type: ignore[arg-type]
            verification_status=verification_status,
            provenance="ingest",
        )
