"""expression-practice 写作伙伴流程：模型 round-trip + explore/select/feedback/finish 状态。"""

import pytest

from finch.practice.models import (
    LocalFeedback,
    PracticeLesson,
    PracticeOption,
    PracticeOptionsOutput,
    PracticeSession,
)
from finch.practice.service import PracticeService
from finch.storage.repositories import PracticeSessionRepository
from finch.storage.workspace import Workspace


def _opt(name, familiarity="熟悉", dimension="结构"):
    return PracticeOption(
        name=name,
        familiarity=familiarity,
        entry_point=f"切入{name}",
        progression=["第一步", "第二步"],
        effect="阅读效果",
        cost="代价",
        facts_needed="",
        dimension=dimension,
    )


def _options():
    return PracticeOptionsOutput(
        options=[_opt("熟悉写法"), _opt("相邻写法", "相邻"), _opt("陌生写法", "陌生")]
    )


def _feedback():
    return LocalFeedback(
        keep="保留这一句",
        key_location="这里当前只是断言",
        alternative_a="写法 A",
        alternative_b="写法 B",
        difference="A 稳、B 有现场感",
        rewrite_task="请重写这一句",
    )


class PartnerRunner:
    def __init__(self, options=None, feedback=None, lesson=None):
        self.options = options or _options()
        self.feedback = feedback or _feedback()
        self.lesson = lesson or PracticeLesson(lesson="先写具体场景再下判断")

    def run(self, prompt, output_model, **kw):
        if output_model is PracticeOptionsOutput:
            return self.options
        if output_model is LocalFeedback:
            return self.feedback
        if output_model is PracticeLesson:
            return self.lesson
        raise AssertionError(output_model)


def _service(tmp_path, runner=None):
    return PracticeService(
        PracticeSessionRepository(Workspace(tmp_path)), runner or PartnerRunner()
    )


# —— 模型 round-trip ——


def test_practice_option_and_local_feedback_round_trip():
    opt = _opt("写法", "陌生", "节奏")
    data = opt.model_dump(mode="json")
    assert PracticeOption(**data) == opt

    fb = _feedback()
    data = fb.model_dump(mode="json")
    assert LocalFeedback(**data) == fb


def test_practice_session_new_fields_round_trip(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.explore(s.id)
    s = svc.select(s.id, 1, reason="想试相邻")
    data = s.model_dump(mode="json")
    reloaded = PracticeSession(**data)
    assert reloaded.source_material == "一个想法"
    assert reloaded.phase == "drafting"
    assert reloaded.selected_option == 1
    assert len(reloaded.options) == 3
    assert reloaded.practice_dimension == "结构"


def test_legacy_session_defaults_new_fields(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    path = ws.dir("practice") / "practice_legacy.yaml"
    path.write_text(
        "id: practice_legacy\n"
        "initial_attempt: 首稿\n"
        "status: started\n"
        "created_at: 2026-01-01T00:00:00+00:00\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
    )
    s = PracticeSessionRepository(ws).get("practice_legacy")
    assert s is not None
    assert s.source_material == ""
    assert s.phase is None
    assert s.options == []
    assert s.selected_option is None
    assert s.feedback_rounds == []
    assert s.final_source == "user_authored"
    assert s.source_note == ""


# —— 新流程状态机 ——


def test_explore_select_first_draft_feedback_finish(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    assert s.initial_attempt == ""
    assert s.source_material == "一个想法"

    s = svc.explore(s.id)
    assert len(s.options) == 3
    assert s.phase == "explore"

    s = svc.select(s.id, 1, reason="想试相邻")
    assert s.selected_option == 1
    assert s.practice_dimension == "结构"
    assert s.phase == "drafting"

    s = svc.save_revision(s.id, "我的首稿")
    assert s.initial_attempt == "我的首稿"
    assert s.revisions == []

    s = svc.feedback(s.id)
    assert len(s.feedback_rounds) == 1
    assert s.phase == "feedback"

    s = svc.save_revision(s.id, "我的重写")
    assert s.revisions == ["我的重写"]

    s = svc.finish(s.id, "最终版", final_source="mixed", source_note="借用了写法 A 的句子")
    assert s.status == "finished"
    assert s.phase == "done"
    assert s.final_source == "mixed"
    assert s.source_note == "借用了写法 A 的句子"


def test_explore_is_idempotent(tmp_path):
    calls: list = []

    class CountingRunner(PartnerRunner):
        def run(self, prompt, output_model, **kw):
            if output_model is PracticeOptionsOutput:
                calls.append(prompt)
            return super().run(prompt, output_model, **kw)

    svc = _service(tmp_path, CountingRunner())
    s = svc.start(material="一个想法")
    s = svc.explore(s.id)
    s = svc.explore(s.id)  # 已有方案，不重复调用 LLM
    assert len(calls) == 1
    assert len(s.options) == 3


def test_start_with_existing_paragraph_is_first_draft(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="已有段落")
    assert s.initial_attempt == "已有段落"
    s = svc.explore(s.id)
    assert len(s.options) == 3
    s = svc.save_revision(s.id, "修改")
    assert s.initial_attempt == "已有段落"
    assert s.revisions == ["修改"]


def test_select_without_options_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    with pytest.raises(ValueError):
        svc.select(s.id, 0)


def test_select_out_of_range_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.explore(s.id)
    with pytest.raises(ValueError):
        svc.select(s.id, 5)


def test_feedback_without_text_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    with pytest.raises(ValueError):
        svc.feedback(s.id)


def test_operations_on_finished_session_rejected(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="首稿")
    s = svc.finish(s.id, "最终版")
    assert s.status == "finished"
    for op in (lambda: svc.explore(s.id), lambda: svc.select(s.id, 0), lambda: svc.feedback(s.id)):
        with pytest.raises(ValueError):
            op()
