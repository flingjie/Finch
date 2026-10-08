"""PracticeService：start / diagnose / respond / save_revision / finish。"""

import pytest

from finch.practice import service as practice_service
from finch.practice.models import PracticeFeedback, PracticeLesson
from finch.practice.service import PracticeService
from finch.storage.repositories import PracticeSessionRepository
from finch.storage.workspace import Workspace


def _fb(action, task=None, diagnosis="最大问题", evidence_quote="原文", hint_level=0):
    return PracticeFeedback(
        action=action,
        diagnosis=diagnosis,
        evidence_quote=evidence_quote,
        task=task,
        hint_level=hint_level,
    )


class FakeRunner:
    def __init__(self, feedbacks=None, lesson=None):
        self.feedbacks = list(feedbacks or [])
        self.lesson = lesson or PracticeLesson(lesson="先讲具体场景再下判断")

    def run(self, prompt, output_model, **kw):
        if output_model is PracticeFeedback:
            if not self.feedbacks:
                raise AssertionError("no feedback left in FakeRunner")
            return self.feedbacks.pop(0)
        if output_model is PracticeLesson:
            return self.lesson
        raise AssertionError(output_model)


class RaisingRunner:
    def run(self, prompt, output_model, **kw):
        raise RuntimeError("boom")


def _service(tmp_path, feedbacks=None):
    ws = Workspace(tmp_path)
    return PracticeService(PracticeSessionRepository(ws), FakeRunner(feedbacks=feedbacks))


def test_full_session(tmp_path):
    svc = _service(
        tmp_path, feedbacks=[_fb("revise", task="具体发生在哪一步？", diagnosis="空泛")]
    )
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    assert s.status == "started"
    s = svc.diagnose(s.id, context="idea context")
    assert s.questions_asked == ["具体发生在哪一步？"]
    assert len(s.turns) == 1
    s = svc.save_revision(s.id, "修订1")
    assert s.revisions == ["修订1"]
    assert s.turns[0].response == "修订1"
    assert s.turns[0].response_kind == "revision"
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    assert s.final_expression == "最终版"
    assert s.lesson == "先讲具体场景再下判断"


def test_diagnose_missing_session_raises(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    with pytest.raises(KeyError):
        svc.diagnose("nope")


def test_operations_on_finished_session_rejected(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    s = svc.start(idea_id="idea_1", initial_attempt="初稿")
    s = svc.diagnose(s.id)
    turn_id = s.turns[0].id
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    for op in (
        lambda: svc.diagnose(s.id),
        lambda: svc.save_revision(s.id, "修订"),
        lambda: svc.respond(s.id, turn_id, "响应", "revision"),
        lambda: svc.finish(s.id, "再次最终"),
    ):
        with pytest.raises(ValueError):
            op()


def test_finish_action_allows_zero_question(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("finish", task=None, diagnosis="已清楚")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    assert s.turns[-1].feedback.action == "finish"
    assert s.turns[-1].feedback.task is None
    assert s.questions_asked == []
    # finish 建议不算待响应轮次，可再次诊断
    svc2 = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    s2 = svc2.diagnose(s.id)
    assert len(s2.turns) == 2


def test_predict_response_does_not_pollute_revisions(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("predict", task="读者会认为你在建议什么？")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    turn_id = s.turns[0].id
    s = svc.respond(s.id, turn_id, "读者可能认为你在建议禁止 AI 写代码", "prediction")
    assert s.revisions == []
    assert s.final_expression == ""
    assert s.turns[0].response_kind == "prediction"


def test_transfer_response_does_not_pollute_revisions(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("transfer", task="改受众再写一遍")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    turn_id = s.turns[0].id
    s = svc.respond(s.id, turn_id, "迁移后的表达", "transfer")
    assert s.revisions == []
    assert s.final_expression == ""
    assert s.turns[0].response_kind == "transfer"


def test_respond_kind_mismatch_raises(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    with pytest.raises(ValueError):
        svc.respond(s.id, s.turns[0].id, "预测", "prediction")


def test_respond_idempotent_and_conflict(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    turn_id = s.turns[0].id
    s1 = svc.respond(s.id, turn_id, "修订1", "revision")
    s2 = svc.respond(s.id, turn_id, "修订1", "revision")  # 幂等
    assert len(s2.turns) == 1
    assert s2.revisions == ["修订1"]
    assert s1.turns[0].responded_at == s2.turns[0].responded_at
    with pytest.raises(ValueError):
        svc.respond(s.id, turn_id, "修订2", "revision")  # 内容冲突


def test_llm_failure_writes_no_partial_turn(tmp_path):
    ws = Workspace(tmp_path)
    repo = PracticeSessionRepository(ws)
    svc = PracticeService(repo, RaisingRunner())
    s = svc.start(initial_attempt="首稿")
    with pytest.raises(RuntimeError):
        svc.diagnose(s.id)
    reloaded = repo.get(s.id)
    assert reloaded is not None
    assert reloaded.turns == []


def test_repeated_diagnose_returns_pending_turn_without_new_llm(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("revise", task="q")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    s2 = svc.diagnose(s.id)  # 已有未响应轮次，不调用 LLM
    assert len(s2.turns) == 1


def test_save_revision_requires_respond_for_predict(tmp_path):
    svc = _service(tmp_path, feedbacks=[_fb("predict", task="读者会怎么读？")])
    s = svc.start(initial_attempt="首稿")
    s = svc.diagnose(s.id)
    with pytest.raises(ValueError):
        svc.save_revision(s.id, "修订")


def test_start_with_audience_goal(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", audience="Agent 开发者", goal="说清审查瓶颈")
    assert s.context.audience == "Agent 开发者"
    assert s.context.goal == "说清审查瓶颈"


def test_legacy_session_reads_with_defaults(tmp_path):
    ws = Workspace(tmp_path)
    path = ws.dir("practice") / "practice_legacy.yaml"
    path.write_text(
        "id: practice_legacy\n"
        "initial_attempt: 首稿\n"
        "diagnosis: 旧诊断\n"
        "questions_asked:\n  - 旧问题\n"
        "revisions:\n  - 旧修订\n"
        "status: started\n"
        "created_at: 2026-01-01T00:00:00+00:00\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
    )
    session = PracticeSessionRepository(ws).get("practice_legacy")
    assert session is not None
    assert session.context.audience == ""
    assert session.context.goal == ""
    assert session.turns == []
    assert session.initial_attempt == "首稿"


def test_diagnose_prompt_mentions_clarity_rules():
    prompt = practice_service._DIAGNOSE_PROMPT
    assert "CL01" in prompt or "clarity" in prompt.casefold()
    assert "ASD-STE100" in prompt
    assert '"predict"' in prompt and '"transfer"' in prompt and '"finish"' in prompt


def test_start_with_method_id(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    assert s.method_id == "emethod_1"


def test_finish_requires_verdict_when_method(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    with pytest.raises(ValueError) as excinfo:
        svc.finish(s.id, "最终版")
    assert "verdict" in str(excinfo.value).casefold()


def test_finish_with_verdict(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="初稿", method_id="emethod_1")
    s = svc.finish(s.id, "最终版", method_verdict="worth_reuse", method_verdict_note="再用")
    assert s.status == "finished"
    assert s.method_verdict == "worth_reuse"
    assert s.method_verdict_note == "再用"
