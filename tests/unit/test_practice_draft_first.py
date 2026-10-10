"""draft-first：mode / draft / react / set_mode / 来源推导 / 向后兼容。"""

from datetime import UTC, datetime

import pytest

from finch.content.models import DraftBodyOutput
from finch.expression_methods.models import ExpressionMethod
from finch.practice.models import (
    LocalFeedback,
    PracticeDraftMeta,
    PracticeLesson,
    PracticeSession,
    UserAction,
)
from finch.practice.service import PracticeService, derive_final_source
from finch.storage.repositories import PracticeSessionRepository
from finch.storage.workspace import Workspace


class DraftRunner:
    def __init__(self, body="一版草稿", explanation="先写反差", task="看看最后一句"):
        self.body = body
        self.explanation = explanation
        self.task = task
        self.lesson = PracticeLesson(lesson="先讲具体场景再下判断")
        self.calls: list[type] = []

    def run(self, prompt, output_model, **kw):
        self.calls.append(output_model)
        if output_model is DraftBodyOutput:
            return DraftBodyOutput(body=self.body)
        if output_model is PracticeDraftMeta:
            return PracticeDraftMeta(explanation=self.explanation, task=self.task)
        if output_model is PracticeLesson:
            return self.lesson
        raise AssertionError(output_model)


def _service(tmp_path, runner=None):
    return PracticeService(
        PracticeSessionRepository(Workspace(tmp_path)), runner or DraftRunner()
    )


def _action(action, version_id, text=""):
    return UserAction(
        action=action, target_version_id=version_id, text=text, created_at=datetime.now(UTC)
    )


def _method(mid="emethod_1"):
    now = datetime.now(UTC)
    return ExpressionMethod(
        id=mid,
        title="失败开场",
        why_effective="先见损失",
        when_to_use="复盘",
        mini_exercise="写开头",
        created_at=now,
        updated_at=now,
    )


# —— 模型默认 / 向后兼容 ——


def test_start_defaults_to_example(tmp_path):
    assert _service(tmp_path).start(material="一个想法").mode == "example"


def test_model_default_is_independent_for_legacy():
    now = datetime.now(UTC)
    s = PracticeSession(id="practice_x", created_at=now, updated_at=now)
    assert s.mode == "independent"
    assert s.ai_drafts == []
    assert s.user_actions == []
    assert s.final_version_id is None
    assert s.learning_observation == ""


def test_legacy_yaml_reads_defaults(tmp_path):
    import yaml

    ws = Workspace(tmp_path)
    ws.ensure()
    path = ws.dir("practice") / "practice_legacy.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "practice_legacy",
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            },
            sort_keys=False,
            allow_unicode=True,
        )
    )
    s = PracticeSessionRepository(ws).get("practice_legacy")
    assert s is not None
    assert s.mode == "independent"
    assert s.ai_drafts == []
    assert s.user_actions == []
    assert s.final_version_id is None


# —— draft ——


def test_draft_generates_ai_draft(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    assert len(s.ai_drafts) == 1
    d = s.ai_drafts[0]
    assert d.text == "一版草稿"
    assert d.explanation == "先写反差"
    assert d.task == "看看最后一句"
    assert d.parent_version_id is None
    assert d.method_ids == []
    assert s.phase == "drafting"


def test_draft_records_method_ids(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id, methods=[_method()])
    assert s.ai_drafts[0].method_ids == ["emethod_1"]


def test_draft_idempotent(tmp_path):
    runner = DraftRunner()
    svc = _service(tmp_path, runner)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    first_id = s.ai_drafts[0].id
    s = svc.draft(s.id)
    assert len(s.ai_drafts) == 1
    assert s.ai_drafts[0].id == first_id
    assert runner.calls.count(DraftBodyOutput) == 1


def test_draft_regenerate_creates_new_version(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    first = s.ai_drafts[0]
    s = svc.draft(s.id, regenerate=True, instruction="换结尾")
    assert len(s.ai_drafts) == 2
    assert s.ai_drafts[1].id != first.id
    assert s.ai_drafts[1].parent_version_id == first.id


def test_draft_rejected_in_independent_mode(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法", mode="independent")
    with pytest.raises(ValueError):
        svc.draft(s.id)


def test_draft_on_finished_session_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    svc.react(s.id, s.ai_drafts[0].id, "adopt")
    with pytest.raises(ValueError):
        svc.draft(s.id)


# —— react ——


def test_react_adopt_finalizes(tmp_path):
    runner = DraftRunner()
    svc = _service(tmp_path, runner)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    runner.calls.clear()
    s = svc.react(s.id, vid, "adopt")
    assert s.status == "finished"
    assert s.phase == "done"
    assert s.final_version_id == vid
    assert s.final_expression == "一版草稿"
    assert s.final_source == "ai_example"
    assert s.lesson == ""
    assert PracticeLesson not in runner.calls  # adopt 不跑 lesson


def test_react_comment_and_edit_non_terminal(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    s = svc.react(s.id, vid, "comment", text="这句判断太强")
    assert s.status == "started"
    assert len(s.user_actions) == 1
    s = svc.react(s.id, vid, "edit", text="改成更弱的判断")
    assert s.status == "started"
    assert s.phase == "revising"
    assert s.final_version_id == vid


def test_react_validation(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    with pytest.raises(ValueError):
        svc.react(s.id, "nope", "adopt")
    with pytest.raises(ValueError):
        svc.react(s.id, vid, "comment", text="")
    with pytest.raises(ValueError):
        svc.react(s.id, vid, "skip", text="不该有")


# —— save_revision with based_on / feedback_round ——


def test_save_based_on_records_edit(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    s = svc.save_revision(s.id, "我的修改", based_on=vid)
    assert s.final_version_id == vid
    assert any(a.action == "edit" and a.target_version_id == vid for a in s.user_actions)


def test_save_based_on_unknown_version_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    with pytest.raises(ValueError):
        svc.save_revision(s.id, "x", based_on="nope")


def _session_with_feedback(tmp_path) -> str:
    repo = PracticeSessionRepository(Workspace(tmp_path))
    now = datetime.now(UTC)
    session = PracticeSession(
        id="practice_fb",
        initial_attempt="首稿",
        feedback_rounds=[LocalFeedback(keep="保留", rewrite_task="请重写")],
        created_at=now,
        updated_at=now,
    )
    repo.upsert(session)
    return session.id


def test_save_feedback_round_populates_user_rewrite(tmp_path):
    svc = _service(tmp_path)
    sid = _session_with_feedback(tmp_path)
    s = svc.save_revision(sid, "重写版", feedback_round=0)
    assert s.feedback_rounds[0].user_rewrite == "重写版"


def test_save_feedback_round_out_of_range(tmp_path):
    svc = _service(tmp_path)
    sid = _session_with_feedback(tmp_path)
    with pytest.raises(ValueError):
        svc.save_revision(sid, "x", feedback_round=3)


def test_save_feedback_round_conflict(tmp_path):
    svc = _service(tmp_path)
    sid = _session_with_feedback(tmp_path)
    svc.save_revision(sid, "重写版", feedback_round=0)
    with pytest.raises(ValueError):
        svc.save_revision(sid, "另一版", feedback_round=0)


# —— set_mode ——


def test_set_mode(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    assert s.mode == "example"
    assert svc.set_mode(s.id, "independent").mode == "independent"


def test_set_mode_on_finished_raises(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    svc.react(s.id, s.ai_drafts[0].id, "adopt")
    with pytest.raises(ValueError):
        svc.set_mode(s.id, "independent")


# —— derive_final_source ——


def test_derive_final_source_rules():
    assert derive_final_source(final_version_id=None, user_actions=[]) == "user_authored"
    assert (
        derive_final_source(final_version_id="v1", user_actions=[_action("adopt", "v1")])
        == "ai_example"
    )
    assert (
        derive_final_source(final_version_id="v1", user_actions=[_action("edit", "v1", "改")])
        == "mixed"
    )
    assert (
        derive_final_source(final_version_id="v1", user_actions=[_action("comment", "v1", "好")])
        == "ai_example"
    )
    assert (
        derive_final_source(
            final_version_id="v1",
            user_actions=[_action("adopt", "v1"), _action("edit", "v1", "改")],
        )
        == "mixed"
    )


# —— finish 分支 ——


def test_finish_ai_only_skips_lesson(tmp_path):
    runner = DraftRunner()
    svc = _service(tmp_path, runner)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    runner.calls.clear()
    s = svc.finish(s.id, s.ai_drafts[0].text, final_version_id=vid)
    assert s.status == "finished"
    assert s.final_source == "ai_example"
    assert s.lesson == ""
    assert PracticeLesson not in runner.calls


def test_finish_user_authored_runs_lesson(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(initial_attempt="我的首稿")
    s = svc.finish(s.id, "我的最终版")
    assert s.lesson == "先讲具体场景再下判断"
    assert s.final_source == "user_authored"


def test_finish_after_edit_derives_mixed(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    s = svc.save_revision(s.id, "我的修改", based_on=vid)
    s = svc.finish(s.id, "我的修改")
    assert s.final_source == "mixed"


def test_finish_rejects_user_authored_when_derived_ai_example(tmp_path):
    svc = _service(tmp_path)
    s = svc.start(material="一个想法")
    s = svc.draft(s.id)
    vid = s.ai_drafts[0].id
    with pytest.raises(ValueError):
        svc.finish(s.id, "x", final_version_id=vid, final_source="user_authored")
