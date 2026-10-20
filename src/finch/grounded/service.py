"""GroundedService：接地气练习会话状态机（draft → ready → waiting_feedback → reviewed）。

确定性状态转换，不调用 LLM。``hypothesis`` / ``question`` / ``action_draft`` 由
grounded-practice 技能提出、经 CLI 参数传入，本服务原样落库；``initial_understanding``
与 ``revised_understanding`` 只能是用户原话或显式「暂不改变」。
"""

import hashlib
from datetime import UTC, datetime

from finch.grounded.models import (
    ActionType,
    Constraint,
    EvidenceType,
    Feedback,
    GroundedSession,
    Revision,
    StatusLiteral,
    feedback_id_for,
    grounded_session_id_for,
)
from finch.storage.repositories import GroundedSessionRepository

_ALLOWED_ACTIONS = {"observe", "ask", "help", "pause"}


class GroundedService:
    """驱动接地气练习会话生命周期；确定性、无 LLM。"""

    def __init__(self, sessions: GroundedSessionRepository) -> None:
        self.sessions = sessions

    def start(
        self,
        *,
        situation: str,
        content_hash: str | None = None,
        request_id: str | None = None,
        source_refs: list[str] | None = None,
        person_ref: str | None = None,
        constraints: list[Constraint] | None = None,
        coverage: list[str] | None = None,
    ) -> GroundedSession:
        """新建会话（幂等：request_id 或 content_hash 相同 → 复用已有）。"""
        if not situation.strip():
            raise ValueError("situation is required")
        ch = content_hash or hashlib.sha256(situation.encode("utf-8")).hexdigest()
        sid = grounded_session_id_for(content_hash=ch, request_id=request_id)
        existing = self.sessions.get(sid)
        if existing is not None:
            return existing
        now = datetime.now(UTC)
        session = GroundedSession(
            id=sid,
            source_refs=list(source_refs or []),
            person_ref=person_ref,
            situation=situation,
            constraints=list(constraints or []),
            coverage=list(coverage or []),
            created_at=now,
            updated_at=now,
        )
        self.sessions.upsert(session)
        return session

    def reflect(self, session_id: str, *, understanding: str) -> GroundedSession:
        """记录用户自己的理解（draft → draft）；不替他填。"""
        session = self._require_status(session_id, "draft")
        if not understanding.strip():
            raise ValueError("understanding is required")
        return self._save(
            session.model_copy(
                update={
                    "initial_understanding": understanding,
                    "understanding_recorded_at": datetime.now(UTC),
                }
            )
        )

    def action(
        self,
        session_id: str,
        *,
        action_type: ActionType,
        hypothesis: str = "",
        question: str = "",
        action_draft: str = "",
    ) -> GroundedSession:
        """选择动作（draft → ready）；observe/ask/help 要求草稿，pause 无需。"""
        session = self._require_status(session_id, "draft")
        if action_type not in _ALLOWED_ACTIONS:
            raise ValueError(f"invalid action_type: {action_type}")
        if not session.initial_understanding.strip():
            raise ValueError("reflect before choosing an action")
        if action_type != "pause" and not action_draft.strip():
            raise ValueError("action_draft is required for observe/ask/help")
        return self._save(
            session.model_copy(
                update={
                    "action_type": action_type,
                    "hypothesis": hypothesis,
                    "question": question,
                    "action_draft": action_draft,
                    "status": "ready",
                }
            )
        )

    def record_action(self, session_id: str) -> GroundedSession:
        """用户明确记录已行动（ready → waiting_feedback）。"""
        session = self._require_status(session_id, "ready")
        return self._save(
            session.model_copy(
                update={
                    "action_recorded": True,
                    "action_recorded_at": datetime.now(UTC),
                    "status": "waiting_feedback",
                }
            )
        )

    def feedback(
        self,
        session_id: str,
        *,
        text: str,
        evidence_type: EvidenceType,
        source_refs: list[str] | None = None,
        revised_understanding: str | None = None,
        keep: bool = False,
        basis: str = "",
    ) -> GroundedSession:
        """追加真实反馈（去重）；给修正/暂不改变时建 Revision 并转 reviewed。"""
        session = self._require_status(session_id, "waiting_feedback")
        if not text.strip():
            raise ValueError("feedback text is required")
        now = datetime.now(UTC)
        fb = Feedback(
            id=feedback_id_for(text=text, evidence_type=evidence_type),
            text=text,
            source_refs=list(source_refs or []),
            evidence_type=evidence_type,
            recorded_at=now,
        )
        updated = session.model_copy(deep=True)
        if all(f.id != fb.id for f in updated.feedback):
            updated.feedback.append(fb)

        revised = revised_understanding.strip() if revised_understanding else ""
        if keep and revised:
            raise ValueError("--keep and --revised-understanding are mutually exclusive")
        revision = None
        status: StatusLiteral = "waiting_feedback"
        if keep:
            revision = Revision(
                original_understanding=updated.initial_understanding,
                revised_understanding=updated.initial_understanding,
                basis=basis,
                kept=True,
                recorded_at=now,
            )
            status = "reviewed"
        elif revised:
            kept = revised == updated.initial_understanding.strip()
            revision = Revision(
                original_understanding=updated.initial_understanding,
                revised_understanding=revised,
                basis=basis,
                kept=kept,
                recorded_at=now,
            )
            status = "reviewed"
        updates: dict = {"feedback": updated.feedback}
        if revision is not None:
            updates["revision"] = revision
            updates["status"] = status
        return self._save(session.model_copy(update=updates))

    def pause(self, session_id: str) -> GroundedSession:
        """中断会话（draft/ready/waiting_feedback → paused）；reviewed 不可 pause。"""
        session = self._require_status(session_id, "draft", "ready", "waiting_feedback")
        if session.status == "paused":
            return session
        return self._save(
            session.model_copy(
                update={"status": "paused", "paused_from": session.status}
            )
        )

    def resume(self, session_id: str) -> GroundedSession:
        """恢复被中断的会话（paused → paused_from）。"""
        session = self._require_status(session_id, "paused")
        if session.paused_from is None:
            raise ValueError(f"session {session_id} has no paused_from state")
        return self._save(
            session.model_copy(
                update={"status": session.paused_from, "paused_from": None}
            )
        )

    def show(self, session_id: str) -> GroundedSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def list(self, *, status: str | None = None) -> list[GroundedSession]:
        sessions = self.sessions.list_all()
        if status is None or status == "all":
            return sessions
        return [s for s in sessions if s.status == status]

    def _get(self, session_id: str) -> GroundedSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _require_status(self, session_id: str, *allowed: StatusLiteral) -> GroundedSession:
        session = self._get(session_id)
        if session.status not in allowed:
            raise ValueError(
                f"illegal transition: session {session_id} in status {session.status}; "
                f"expected one of {list(allowed)}"
            )
        return session

    def _save(self, session: GroundedSession) -> GroundedSession:
        session = session.model_copy(update={"updated_at": datetime.now(UTC)})
        self.sessions.upsert(session)
        return session
