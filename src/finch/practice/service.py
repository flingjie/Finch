"""PracticeService：expression-practice 领域服务（skill 对话驱动 + 一次性 CLI 落库）。

状态机：started →（diagnose / respond / save_revision 可多轮）→ finished。每步幂等落库
（``PracticeSessionRepository.upsert``）。诊断与经验总结是 LLM 开放性判断；其余是确定性状态。
"""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.llm.base import StructuredInferenceRunner
from finch.practice.models import (
    ActionLiteral,
    ExerciseLiteral,
    FinalSourceLiteral,
    LocalFeedback,
    MethodVerdictLiteral,
    PracticeContext,
    PracticeFeedback,
    PracticeLesson,
    PracticeOptionsOutput,
    PracticeSession,
    PracticeTurn,
    ResponseKindLiteral,
)
from finch.storage.repositories import PracticeSessionRepository

_DIAGNOSE_PROMPT = """\
You are a writing coach. Look at the user's latest expression of an idea and identify the single
biggest obstacle to the target reader's understanding, then choose ONE next action.

Check dimensions: real understanding / own judgment / causality / relevance to the peer /
specificity / sounds like the author / leaves room to respond /
clarity (ASD-STE100-inspired CL01–CL08: one idea per sentence, clear actor/action,
consistent terms, concrete support, prerequisites near advice, executable steps,
one topic per paragraph, meaning preserved).
When the user asked to practice clarity, prefer the single biggest clarity obstacle.

Actions (pick exactly one):
- "revise": the expression has a fixable obstacle; put ONE guiding question in `task`.
- "predict": ask the user to predict what a reader would conclude; put that prediction task in
  `task`. Mark it as a simulation of reading, not real reader research.
- "hint": the user explicitly asked for help; put minimal help in `task` at the requested level.
- "transfer": the current expression is already clear; put a transfer task in `task` that changes
  ONE of audience / length / example. Only offer transfer when it serves the practice goal.
- "finish": the expression already meets the goal; return `task` empty to end the practice.

Rules: quote ONE piece of the user's text in `evidence_quote` and explain why it blocks this
reader. Do not list a multi-dimension score. Do not invent the user's experiences, effect
numbers, or stance. Do not treat many technical words as poor understanding. Keep the
ASD-STE100-inspired clarity criteria but do not shorten text at the cost of meaning.
`hint_level` is non-zero only for "hint".

## Target
audience: {audience}
goal: {goal}

## Idea context
{context}

## Initial attempt
{initial}

## Revisions so far
{revisions}

## Latest expression
{latest}

## Turn history
{history}

## Requested exercise (preference only; still pick the action that helps most, or "finish")
{exercise}
hint_level requested: {hint_level}

Respond with JSON matching the schema: action, diagnosis (the single biggest obstacle),
evidence_quote (one verbatim quote), task (the next action content, or empty for "finish"),
hint_level (0 unless action is "hint").
"""

_LESSON_PROMPT = """\
Compare the user's initial attempt with their final expression and write one lesson learned
about their expression, not a list of generic advice.

## Initial attempt
{initial}

## Final expression
{final}

## Transfer exercises completed
{transfers}

Respond with JSON matching the schema: lesson (one specific lesson). If no transfer exercise
was completed, do not claim transfer was verified.
"""

_OPTIONS_PROMPT = """\
You are a writing partner. Given the user's material, propose THREE distinct ways to develop
it. They must be genuinely different approaches (not the same structure relabeled): one
familiar to the user, one adjacent variation, one unfamiliar attempt. Each must still fit
the material.

For each option give:
- name: a short approach name.
- familiarity: one of 熟悉 / 相邻 / 陌生 / 暂定 (use 暂定 when no practice history is given).
- entry_point: how this approach enters the material.
- progression: 2-3 concrete steps.
- effect: the reading effect this approach aims for.
- cost: what the author or reader gives up.
- facts_needed: facts or details the user must supply for this to work (empty if none).
- dimension: the ONE main dimension this approach exercises (结构 / 节奏 / 手法 / 语气, or a
  specific sub-dimension).

Rules:
- Change ONE main dimension per option so the user can see the effect clearly.
- Do NOT write a full opening, full text, or a sentence-by-sentence outline.
- Do NOT invent the user's experiences, effect numbers, dialogue, or certain conclusions.
- Unfamiliar options must still fit the material; do not pad with irrelevant novelty.
- Mark the user's familiarity honestly: with no history use 暂定, do not fabricate preferences.

## Material
{material}

## Practice history (may be empty)
{history}

## Goal
goal: {goal}
audience: {audience}

Respond with JSON matching the schema: options (list of exactly 3 items).
"""

_FEEDBACK_PROMPT = """\
You are a writing partner. Review the user's current text and give ONE local comparison
feedback, following a fixed structure. Use only facts already in the text; do not invent
experiences, effect numbers, dialogue, or certain conclusions. "How readers will react" is a
hypothesis, not real feedback.

## Selected approach
{option}

## Main practice dimension
{dimension}

## User's current text
{text}

Fill each field:
- keep: quote ONE phrase the user wrote and say why it works.
- key_location: locate ONE sentence or short fragment and state its current effect.
- alternative_a: a short alternative for that same location (do NOT expand the whole
  paragraph).
- alternative_b: a second short alternative for that same location.
- difference: how A and B differ in rhythm / tone / technique / development, and what each
  costs.
- rewrite_task: invite the user to write their own version (A, B, or a third).

Keep alternatives short; do not write the full text or a sentence-by-sentence outline.

Respond with JSON matching the schema: keep, key_location, alternative_a, alternative_b,
difference, rewrite_task.
"""


class PracticeService:
    """驱动一次表达练习会话。"""

    _ACTION_TO_KIND: dict[str, str] = {
        "revise": "revision",
        "hint": "revision",
        "predict": "prediction",
        "transfer": "transfer",
    }

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
        initial_attempt: str = "",
        material: str = "",
        method_id: str | None = None,
        audience: str = "",
        goal: str = "",
    ) -> PracticeSession:
        """创建会话，记 initial_attempt（可空）与目标语境（audience / goal）。

        新流程支持「先探索、用户再写首稿」：仅给 ``material`` 时 ``initial_attempt`` 留空，
        首条 ``save_revision`` 再写入首稿。
        """
        now = datetime.now(UTC)
        session = PracticeSession(
            id=f"practice_{uuid4().hex[:8]}",
            idea_id=idea_id,
            initial_attempt=initial_attempt,
            source_material=material,
            method_id=method_id,
            context=PracticeContext(audience=audience, goal=goal),
            created_at=now,
            updated_at=now,
        )
        self.sessions.upsert(session)
        return session

    def explore(
        self, session_id: str, *, material: str = "", history: str = ""
    ) -> PracticeSession:
        """生成三种写法方案，写入 options，置 phase=explore。幂等：已有方案则不重算。

        仅当会话尚无首稿与方案时才接受 ``material``（作为原始素材落库）。
        """
        session = self._require_started(session_id)
        if material and not session.source_material and not session.initial_attempt:
            session = session.model_copy(
                update={"source_material": material, "updated_at": datetime.now(UTC)}
            )
        if session.options:
            return session
        source = session.initial_attempt or session.source_material
        out = cast(
            PracticeOptionsOutput,
            self.runner.run(
                _OPTIONS_PROMPT.format(
                    material=source or material,
                    history=history,
                    goal=session.context.goal,
                    audience=session.context.audience,
                ),
                PracticeOptionsOutput,
            ),
        )
        session = session.model_copy(
            update={
                "options": out.options,
                "phase": "explore",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def select(
        self, session_id: str, option_index: int, *, reason: str = ""
    ) -> PracticeSession:
        """记录用户选择的方案与理由，置 phase=drafting。"""
        session = self._require_started(session_id)
        if not session.options:
            raise ValueError("no options to select from; run explore first")
        if option_index < 0 or option_index >= len(session.options):
            raise ValueError(
                f"option index {option_index} out of range (0..{len(session.options) - 1})"
            )
        chosen = session.options[option_index]
        session = session.model_copy(
            update={
                "selected_option": option_index,
                "selection_reason": reason,
                "practice_dimension": chosen.dimension,
                "phase": "drafting",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def feedback(self, session_id: str) -> PracticeSession:
        """针对用户当前文本生成一轮局部对比反馈，追加 feedback_rounds，置 phase=feedback。"""
        session = self._require_started(session_id)
        text = session.revisions[-1] if session.revisions else session.initial_attempt
        if not text.strip():
            raise ValueError("no text to give feedback on; write a first draft first")
        chosen = ""
        if session.selected_option is not None and 0 <= session.selected_option < len(
            session.options
        ):
            chosen = session.options[session.selected_option].name
        out = cast(
            LocalFeedback,
            self.runner.run(
                _FEEDBACK_PROMPT.format(
                    option=chosen or "（未选择）",
                    dimension=session.practice_dimension,
                    text=text,
                ),
                LocalFeedback,
            ),
        )
        session = session.model_copy(
            update={
                "feedback_rounds": [*session.feedback_rounds, out],
                "phase": "feedback",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def diagnose(
        self,
        session_id: str,
        *,
        context: str = "",
        exercise: ExerciseLiteral = "auto",
        hint_level: int = 0,
    ) -> PracticeSession:
        """LLM 诊断一个最大障碍并选定下一步动作，追加一条 PracticeTurn。

        已有未响应轮次时不重复诊断，直接返回会话（避免同一文本反复累积问题）。
        """
        session = self._require_started(session_id)
        if self._pending_turn(session) is not None:
            return session
        latest = session.revisions[-1] if session.revisions else session.initial_attempt
        out = cast(
            PracticeFeedback,
            self.runner.run(
                _DIAGNOSE_PROMPT.format(
                    audience=session.context.audience,
                    goal=session.context.goal,
                    context=context,
                    initial=session.initial_attempt,
                    revisions="\n---\n".join(session.revisions),
                    latest=latest,
                    history=self._render_history(session),
                    exercise=exercise,
                    hint_level=hint_level,
                ),
                PracticeFeedback,
            ),
        )
        turn = PracticeTurn(
            id=f"turn_{uuid4().hex[:8]}",
            expression_snapshot=latest,
            feedback=out,
            created_at=datetime.now(UTC),
        )
        update: dict = {
            "turns": [*session.turns, turn],
            "diagnosis": out.diagnosis,
            "updated_at": datetime.now(UTC),
        }
        if out.action in ("revise", "predict", "transfer"):
            update["questions_asked"] = [*session.questions_asked, out.task]
        session = session.model_copy(update=update)
        self.sessions.upsert(session)
        return session

    def respond(
        self,
        session_id: str,
        turn_id: str,
        response: str,
        kind: ResponseKindLiteral,
    ) -> PracticeSession:
        """对一轮反馈作出响应（revision / prediction / transfer / skipped）。

        revision 响应写入 revisions（成为最新正文）；predict / transfer 不污染正文。
        同一轮次相同内容重试幂等；不同内容冲突报错。
        """
        session = self._require_started(session_id)
        turn = self._find_turn(session, turn_id)
        if turn.responded_at is not None:
            if turn.response == response and turn.response_kind == kind:
                return session
            raise ValueError(f"turn {turn_id} already answered; diagnose for a new turn")
        self._validate_kind(turn.feedback.action, kind)
        revisions = [*session.revisions, response] if kind == "revision" else session.revisions
        session = session.model_copy(
            update={
                "turns": self._complete_turn(session, turn_id, response, kind),
                "revisions": revisions,
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def save_revision(self, session_id: str, revision: str) -> PracticeSession:
        """追加一次修订；有待处理 revise/hint 轮次时同时完成该轮。

        尚无首稿时（新流程「先探索、再首写」），第一条修订写入 ``initial_attempt``，
        后续才追加到 ``revisions``。
        """
        session = self._require_started(session_id)
        pending = self._pending_turn(session)
        if not session.initial_attempt:
            update: dict = {
                "initial_attempt": revision,
                "updated_at": datetime.now(UTC),
            }
        else:
            update = {
                "revisions": [*session.revisions, revision],
                "updated_at": datetime.now(UTC),
            }
        if pending is not None:
            if pending.feedback.action in ("revise", "hint"):
                update["turns"] = self._complete_turn(
                    session, pending.id, revision, "revision"
                )
            else:
                raise ValueError(
                    f"pending {pending.feedback.action} turn {pending.id} requires "
                    "respond, not save"
                )
        session = session.model_copy(update=update)
        self.sessions.upsert(session)
        return session

    def finish(
        self,
        session_id: str,
        final_expression: str,
        *,
        method_verdict: MethodVerdictLiteral | None = None,
        method_verdict_note: str = "",
        final_source: FinalSourceLiteral = "user_authored",
        source_note: str = "",
    ) -> PracticeSession:
        """记最终版 + LLM 生成 lesson，置 finished；方法练习必须给 verdict。

        ``final_source`` 区分 user_authored / ai_example / mixed；``source_note`` 记混合
        文本的来源片段。风格证据只引用可追溯的用户创作片段。
        """
        session = self._require_started(session_id)
        if session.method_id and method_verdict is None:
            raise ValueError("method_verdict required when session has method_id")
        lesson = cast(
            PracticeLesson,
            self.runner.run(
                _LESSON_PROMPT.format(
                    initial=session.initial_attempt,
                    final=final_expression,
                    transfers=self._render_transfers(session),
                ),
                PracticeLesson,
            ),
        )
        session = session.model_copy(
            update={
                "final_expression": final_expression,
                "lesson": lesson.lesson,
                "status": "finished",
                "phase": "done",
                "method_verdict": method_verdict,
                "method_verdict_note": method_verdict_note,
                "final_source": final_source,
                "source_note": source_note,
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
        """只允许对 started 会话做 diagnose/respond/save_revision/finish。"""
        session = self._get(session_id)
        if session.status != "started":
            raise ValueError(
                f"illegal transition: session {session_id} in status {session.status}; "
                "expected started"
            )
        return session

    def _pending_turn(self, session: PracticeSession) -> PracticeTurn | None:
        """返回待响应的轮次；finish 建议不算待响应。"""
        for turn in session.turns:
            if turn.responded_at is None and turn.feedback.action != "finish":
                return turn
        return None

    def _find_turn(self, session: PracticeSession, turn_id: str) -> PracticeTurn:
        for turn in session.turns:
            if turn.id == turn_id:
                return turn
        raise ValueError(f"turn not found: {turn_id}")

    def _validate_kind(self, action: ActionLiteral, kind: ResponseKindLiteral) -> None:
        if action == "finish":
            raise ValueError("finish turn does not accept a response; run finish instead")
        if kind == "skipped":
            return
        expected = self._ACTION_TO_KIND.get(action)
        if kind != expected:
            raise ValueError(f"action {action} expects kind {expected!r}, got {kind!r}")

    def _complete_turn(
        self, session: PracticeSession, turn_id: str, response: str, kind: ResponseKindLiteral
    ) -> list[PracticeTurn]:
        now = datetime.now(UTC)
        new_turns = []
        for turn in session.turns:
            if turn.id == turn_id:
                turn = turn.model_copy(
                    update={
                        "response": response,
                        "response_kind": kind,
                        "responded_at": now,
                    }
                )
            new_turns.append(turn)
        return new_turns

    def _render_history(self, session: PracticeSession) -> str:
        if not session.turns:
            return "（无）"
        lines = []
        for turn in session.turns:
            response = turn.response if turn.response is not None else "（未响应）"
            lines.append(
                f"- action={turn.feedback.action}, diagnosis={turn.feedback.diagnosis}, "
                f"response={response}"
            )
        return "\n".join(lines)

    def _render_transfers(self, session: PracticeSession) -> str:
        transfers = [
            turn for turn in session.turns
            if turn.feedback.action == "transfer" and turn.response
        ]
        if not transfers:
            return "（无）"
        lines = []
        for turn in transfers:
            lines.append(f"- task: {turn.feedback.task}\n  response: {turn.response}")
        return "\n".join(lines)
