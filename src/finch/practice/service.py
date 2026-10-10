"""PracticeService：expression-practice 领域服务（skill 对话驱动 + 一次性 CLI 落库）。

状态机：started →（diagnose / respond / save_revision 可多轮）→ finished。每步幂等落库
（``PracticeSessionRepository.upsert``）。诊断与经验总结是 LLM 开放性判断；其余是确定性状态。
"""

import hashlib
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.content.voice import VoiceProfile
from finch.content.writer import render_draft_body
from finch.expression_methods.models import ExpressionMethod
from finch.llm.base import StructuredInferenceRunner
from finch.practice.models import (
    ActionLiteral,
    AiDraft,
    ExerciseLiteral,
    FinalSourceLiteral,
    LocalFeedback,
    MethodVerdictLiteral,
    ModeLiteral,
    PracticeContext,
    PracticeDraftMeta,
    PracticeFeedback,
    PracticeLesson,
    PracticeOptionsOutput,
    PracticeSession,
    PracticeTurn,
    ResponseKindLiteral,
    UserAction,
    UserActionLiteral,
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

_PRACTICE_META_PROMPT = """\
You are a writing partner. Given the draft you just produced and the material, explain ONE
specific writing choice the draft makes and its trade-off, then propose ONE optional micro-action
for the user (leave task empty if none fits).

Do not write a general writing lesson. Explain a concrete choice in THIS draft (structure,
opening, detail, or wording) and what it costs or gains. The micro-action, when present, invites
the user to change or judge ONE small thing (for example: whether the last sentence states what
they actually intend), not multiple tasks.

## Draft
{text}

## Material / target context
{context}

Respond with JSON matching the schema: explanation (one specific choice + its cost), task
(one optional micro-action, or empty).
"""


def derive_final_source(
    *,
    final_version_id: str | None,
    user_actions: list[UserAction],
) -> FinalSourceLiteral:
    """从版本与用户动作推导最终版来源（纯函数，测试可直接调用）。

    规则：无版本 → user_authored；有 edit 指向该版本 → mixed；有 adopt 且无 edit →
    ai_example；只有 comment/skip → ai_example。片段无法确定来源时由风格观察层排除，
    不在此判定。
    """
    if final_version_id is None:
        return "user_authored"
    for action in user_actions:
        if action.action == "edit" and action.target_version_id == final_version_id:
            return "mixed"
    return "ai_example"


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
        mode: ModeLiteral = "example",
    ) -> PracticeSession:
        """创建会话，记 initial_attempt（可空）与目标语境（audience / goal）。

        新流程支持「先探索、用户再写首稿」：仅给 ``material`` 时 ``initial_attempt`` 留空，
        首条 ``save_revision`` 再写入首稿。新会话默认 ``mode=example``（草稿优先），
        旧 YAML 缺省读作 independent。
        """
        now = datetime.now(UTC)
        session = PracticeSession(
            id=f"practice_{uuid4().hex[:8]}",
            idea_id=idea_id,
            initial_attempt=initial_attempt,
            source_material=material,
            method_id=method_id,
            context=PracticeContext(audience=audience, goal=goal),
            mode=mode,
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

    def draft(
        self,
        session_id: str,
        *,
        instruction: str = "",
        regenerate: bool = False,
        methods: list[ExpressionMethod] | None = None,
        voice_profile: VoiceProfile | None = None,
    ) -> PracticeSession:
        """生成一版 AI 草稿 + 一条写法说明 + 一个可选小动作，追加 ai_drafts。

        independent 模式不给范文；``draft`` 幂等（同输入同 request key 复用同一版本），
        ``regenerate`` 显式创建新版本（旧版保留、parent_version_id 指向上一个 head）。
        """
        session = self._require_started(session_id)
        if session.mode == "independent":
            raise ValueError("draft is not available in independent mode")
        methods = methods or []
        head = session.ai_drafts[-1] if session.ai_drafts else None
        method_ids = sorted(m.id for m in methods)
        request_key = "\x1f".join(
            [
                session.id,
                instruction,
                session.mode,
                ",".join(method_ids),
                self._material_snapshot(session),
            ]
        )
        if regenerate:
            draft_id = f"aid_{uuid4().hex[:12]}"
        else:
            draft_id = "aid_" + hashlib.sha256(request_key.encode()).hexdigest()[:12]
            for existing in session.ai_drafts:
                if existing.id == draft_id:
                    return session
        body = render_draft_body(
            self.runner,
            context=self._render_practice_context(session, methods),
            voice_profile=voice_profile,
        )
        meta = cast(
            PracticeDraftMeta,
            self.runner.run(
                _PRACTICE_META_PROMPT.format(
                    text=body,
                    context=self._render_practice_context(session, methods),
                ),
                PracticeDraftMeta,
            ),
        )
        draft = AiDraft(
            id=draft_id,
            text=body,
            parent_version_id=head.id if head else None,
            method_ids=method_ids,
            explanation=meta.explanation,
            task=meta.task,
            created_at=datetime.now(UTC),
        )
        session = session.model_copy(
            update={
                "ai_drafts": [*session.ai_drafts, draft],
                "phase": "drafting",
                "updated_at": datetime.now(UTC),
            }
        )
        self.sessions.upsert(session)
        return session

    def react(
        self,
        session_id: str,
        version_id: str,
        action: UserActionLiteral,
        *,
        text: str = "",
        edit_scope: str = "",
    ) -> PracticeSession:
        """记录用户对某 AI 版本的动作；adopt 收尾（不跑 lesson、不宣称学习）。"""
        session = self._require_started(session_id)
        draft = self._find_draft(session, version_id)
        if action in ("comment", "edit") and not text.strip():
            raise ValueError(f"{action} action requires non-empty text")
        if action == "skip" and text:
            raise ValueError("skip action must not carry text")
        if edit_scope and action != "edit":
            raise ValueError("edit_scope is only allowed for edit action")
        user_action = UserAction(
            action=action,
            target_version_id=version_id,
            text=text,
            edit_scope=edit_scope,
            created_at=datetime.now(UTC),
        )
        if action == "adopt":
            session = session.model_copy(
                update={
                    "user_actions": [*session.user_actions, user_action],
                    "final_version_id": version_id,
                    "final_expression": draft.text,
                    "final_source": "ai_example",
                    "source_note": f"adopted ai draft {version_id} verbatim",
                    "status": "finished",
                    "phase": "done",
                    "lesson": "",
                    "learning_observation": "adopted AI draft; no user-authored creation",
                    "updated_at": datetime.now(UTC),
                }
            )
        else:
            update: dict = {
                "user_actions": [*session.user_actions, user_action],
                "updated_at": datetime.now(UTC),
            }
            if action == "edit":
                update["phase"] = "revising"
                update["final_version_id"] = version_id
            session = session.model_copy(update=update)
        self.sessions.upsert(session)
        return session

    def set_mode(self, session_id: str, mode: ModeLiteral) -> PracticeSession:
        """切换会话模式（不清空已保存文本）。"""
        session = self._require_started(session_id)
        session = session.model_copy(
            update={"mode": mode, "updated_at": datetime.now(UTC)}
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

    def save_revision(
        self,
        session_id: str,
        revision: str,
        *,
        based_on: str | None = None,
        feedback_round: int | None = None,
    ) -> PracticeSession:
        """追加一次修订；有待处理 revise/hint 轮次时同时完成该轮。

        尚无首稿时（新流程「先探索、再首写」），第一条修订写入 ``initial_attempt``，
        后续才追加到 ``revisions``。``based_on`` 关联 AI 版本（记录 edit 动作）；
        ``feedback_round`` 关联明确的反馈轮（写 ``LocalFeedback.user_rewrite``）。
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
        if based_on is not None:
            self._find_draft(session, based_on)
            update["user_actions"] = [
                *session.user_actions,
                UserAction(
                    action="edit",
                    target_version_id=based_on,
                    text=revision,
                    created_at=datetime.now(UTC),
                ),
            ]
            update["final_version_id"] = based_on
            update["phase"] = "revising"
        if feedback_round is not None:
            rounds = session.feedback_rounds
            if feedback_round < 0 or feedback_round >= len(rounds):
                raise ValueError(
                    f"feedback_round {feedback_round} out of range (0..{len(rounds) - 1})"
                )
            current = rounds[feedback_round].user_rewrite
            if current and current != revision:
                raise ValueError(
                    f"feedback_round {feedback_round} already has a different user_rewrite"
                )
            if not current:
                rounds = [
                    r.model_copy(update={"user_rewrite": revision})
                    if i == feedback_round
                    else r
                    for i, r in enumerate(rounds)
                ]
                update["feedback_rounds"] = rounds
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
        final_source: FinalSourceLiteral | None = None,
        source_note: str = "",
        final_version_id: str | None = None,
        learning_observation: str = "",
    ) -> PracticeSession:
        """记最终版，置 finished；有用户创作时跑 lesson，仅 AI 稿时不跑。

        ``final_source`` 缺省时由版本与用户动作推导；推导为 ai_example 时禁止传
        user_authored。风格证据只引用可追溯的用户创作片段。
        """
        session = self._require_started(session_id)
        if session.method_id and method_verdict is None:
            raise ValueError("method_verdict required when session has method_id")
        effective_version_id = (
            final_version_id if final_version_id is not None else session.final_version_id
        )
        derived = derive_final_source(
            final_version_id=effective_version_id,
            user_actions=session.user_actions,
        )
        if final_source is None:
            final_source = derived
        elif derived == "ai_example" and final_source != "ai_example":
            raise ValueError("adopted AI draft cannot be marked user_authored")
        has_user_creation = final_source in ("user_authored", "mixed")
        update: dict = {
            "final_expression": final_expression,
            "status": "finished",
            "phase": "done",
            "method_verdict": method_verdict,
            "method_verdict_note": method_verdict_note,
            "final_source": final_source,
            "source_note": source_note,
            "final_version_id": effective_version_id,
            "updated_at": datetime.now(UTC),
        }
        if has_user_creation:
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
            update["lesson"] = lesson.lesson
            update["learning_observation"] = learning_observation
        else:
            update["lesson"] = ""
            update["learning_observation"] = learning_observation or (
                "read/adopted AI draft; no learning summary claimed"
            )
        session = session.model_copy(update=update)
        self.sessions.upsert(session)
        return session

    def _find_draft(self, session: PracticeSession, version_id: str) -> AiDraft:
        for draft in session.ai_drafts:
            if draft.id == version_id:
                return draft
        raise ValueError(f"version not found: {version_id}")

    def _material_snapshot(self, session: PracticeSession) -> str:
        """素材与用户文本的拼接（供 draft 幂等 key）。"""
        return "\x1f".join(
            [session.source_material, session.initial_attempt, *session.revisions]
        )

    def _render_practice_context(
        self, session: PracticeSession, methods: list[ExpressionMethod]
    ) -> str:
        """把模式/素材/用户文本/目标/已选写法/方法体拼成 practice 语境块（复用 draft prompt）。"""
        mode_guide = {
            "example": "写一版完整、可独立阅读的草稿。",
            "guided": "只写开头或框架，不写完整正文；给用户留出需要补写的段落。",
        }.get(session.mode, "写一版完整、可独立阅读的草稿。")
        blocks = [
            "## Mode",
            mode_guide,
            "## Material",
            session.source_material or "(none)",
            "## User's latest text",
            (session.revisions[-1] if session.revisions else session.initial_attempt)
            or "(none)",
            "## Target",
            f"audience: {session.context.audience or '(none)'}",
            f"goal: {session.context.goal or '(none)'}",
        ]
        if session.selected_option is not None and 0 <= session.selected_option < len(
            session.options
        ):
            opt = session.options[session.selected_option]
            blocks += [
                "## Selected approach",
                f"name: {opt.name}",
                f"dimension: {opt.dimension}",
                f"entry_point: {opt.entry_point}",
                f"progression: {' -> '.join(opt.progression)}",
            ]
        for m in methods:
            blocks += [
                "## Expression method",
                f"id: {m.id}",
                f"title: {m.title}",
                f"why_effective: {m.why_effective}",
                f"when_to_use: {m.when_to_use}",
                f"boundaries: {m.boundaries}",
                f"mini_exercise: {m.mini_exercise}",
            ]
        return "\n".join(blocks) + "\n\n"

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
