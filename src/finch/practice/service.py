"""PracticeService：expression-practice 领域服务（skill 对话驱动 + 一次性 CLI 落库）。

状态机：started →（diagnose / save_revision 可多轮）→ finished。每步幂等落库
（``PracticeSessionRepository.upsert``）。诊断与经验总结是 LLM 开放性判断；其余是确定性状态。
"""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.llm.base import StructuredInferenceRunner
from finch.practice.models import PracticeDiagnosis, PracticeLesson, PracticeSession
from finch.storage.repositories import PracticeSessionRepository

_DIAGNOSE_PROMPT = """\
You are a writing coach. Diagnose the user's latest expression of an idea and identify the
single biggest problem, then ask exactly ONE question to guide the next revision.

Check dimensions: real understanding / own judgment / causality / relevance to the peer /
specificity / sounds like the author / leaves room to respond.

## Idea context
{context}

## Initial attempt
{initial}

## Revisions so far
{revisions}

## Latest expression
{latest}

Respond with JSON matching the schema: diagnosis (the single biggest problem) and
question (exactly one guiding question).
"""

_LESSON_PROMPT = """\
Compare the user's initial attempt with their final expression and write one lesson learned
about their expression, not a list of generic advice.

## Initial attempt
{initial}

## Final expression
{final}

Respond with JSON matching the schema: lesson (one specific lesson).
"""


class PracticeService:
    """驱动一次表达练习会话。"""

    def __init__(
        self,
        sessions: PracticeSessionRepository,
        runner: StructuredInferenceRunner,
    ) -> None:
        self.sessions = sessions
        self.runner = runner

    def start(
        self,
        *,
        idea_id: str | None = None,
        opportunity_id: str | None = None,
        initial_attempt: str,
    ) -> PracticeSession:
        """创建会话，记 initial_attempt。"""
        now = datetime.now(UTC)
        session = PracticeSession(
            id=f"practice_{uuid4().hex[:8]}",
            idea_id=idea_id,
            opportunity_id=opportunity_id,
            initial_attempt=initial_attempt,
            created_at=now,
            updated_at=now,
        )
        self.sessions.upsert(session)
        return session

    def diagnose(self, session_id: str, *, context: str = "") -> PracticeSession:
        """LLM 诊断最大问题 + 追问一个问题；追加到 questions_asked。"""
        session = self._require_started(session_id)
        out = cast(
            PracticeDiagnosis,
            self.runner.run(
                _DIAGNOSE_PROMPT.format(
                    context=context,
                    initial=session.initial_attempt,
                    revisions="\n---\n".join(session.revisions),
                    latest=session.revisions[-1] if session.revisions else session.initial_attempt,
                ),
                PracticeDiagnosis,
            ),
        )
        session = session.model_copy(
            update={
                "diagnosis": out.diagnosis,
                "questions_asked": [*session.questions_asked, out.question],
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def save_revision(self, session_id: str, revision: str) -> PracticeSession:
        """追加一次修订。"""
        session = self._require_started(session_id)
        session = session.model_copy(
            update={
                "revisions": [*session.revisions, revision],
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def finish(self, session_id: str, final_expression: str) -> PracticeSession:
        """记最终版 + LLM 生成 lesson，置 finished。"""
        session = self._require_started(session_id)
        lesson = cast(
            PracticeLesson,
            self.runner.run(
                _LESSON_PROMPT.format(
                    initial=session.initial_attempt, final=final_expression
                ),
                PracticeLesson,
            ),
        )
        session = session.model_copy(
            update={
                "final_expression": final_expression,
                "lesson": lesson.lesson,
                "status": "finished",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def _get(self, session_id: str) -> PracticeSession:
        session = self.sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        return session

    def _require_started(self, session_id: str) -> PracticeSession:
        """只允许对 started 会话做 diagnose/save_revision/finish（状态机 started→finished）。"""
        session = self._get(session_id)
        if session.status != "started":
            raise ValueError(
                f"illegal transition: session {session_id} in status {session.status}; "
                "expected started"
            )
        return session
