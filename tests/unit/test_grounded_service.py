"""GroundedService 状态机单元测试。"""

import pytest

from finch.grounded.service import GroundedService
from finch.storage.repositories import GroundedSessionRepository
from finch.storage.workspace import Workspace


def _svc(tmp_path) -> GroundedService:
    ws = Workspace(tmp_path)
    return GroundedService(GroundedSessionRepository(ws))


def test_start_idempotent(tmp_path):
    svc = _svc(tmp_path)
    a = svc.start(situation="他加班", content_hash="h1")
    b = svc.start(situation="他加班", content_hash="h1")
    assert a.id == b.id
    assert len(svc.list()) == 1
    # request_id 优先
    c = svc.start(situation="他加班", content_hash="h1", request_id="r1")
    assert c.id != a.id
    assert len(svc.list()) == 2


def test_reflect_records_understanding(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="我的理解")
    assert s.initial_understanding == "我的理解"
    assert s.understanding_recorded_at is not None
    assert s.status == "draft"


def test_action_requires_reflect_first(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    with pytest.raises(ValueError, match="reflect"):
        svc.action(s.id, action_type="ask", action_draft="草稿")


def test_action_requires_draft_unless_pause(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="理解")
    with pytest.raises(ValueError, match="action_draft"):
        svc.action(s.id, action_type="ask", action_draft="")
    # pause 无需草稿
    s2 = svc.action(s.id, action_type="pause")
    assert s2.status == "ready"
    assert s2.action_type == "pause"


def test_action_ready_and_record_action(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="理解")
    s = svc.action(s.id, action_type="ask", hypothesis="H", question="Q", action_draft="草稿")
    assert s.status == "ready"
    assert s.hypothesis == "H"
    assert s.question == "Q"
    assert s.action_draft == "草稿"
    s = svc.record_action(s.id)
    assert s.status == "waiting_feedback"
    assert s.action_recorded is True
    assert s.action_recorded_at is not None


def test_feedback_appends_and_dedups(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="原理解")
    s = svc.action(s.id, action_type="ask", action_draft="草稿")
    s = svc.record_action(s.id)
    s = svc.feedback(s.id, text="新信息", evidence_type="user_reported")
    assert len(s.feedback) == 1
    assert s.status == "waiting_feedback"  # 未给修正，保持
    s = svc.feedback(s.id, text="新信息", evidence_type="user_reported")
    assert len(s.feedback) == 1  # 去重


def test_feedback_revised_transitions_to_reviewed(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="原理解")
    s = svc.action(s.id, action_type="help", action_draft="草稿")
    s = svc.record_action(s.id)
    s = svc.feedback(
        s.id, text="反馈", evidence_type="source_quote",
        revised_understanding="修正理解", basis="对方原文",
    )
    assert s.status == "reviewed"
    assert s.revision is not None
    assert s.revision.original_understanding == "原理解"
    assert s.revision.revised_understanding == "修正理解"
    assert s.revision.kept is False
    # 原理解未被覆盖
    assert s.initial_understanding == "原理解"


def test_feedback_keep_transitions_to_reviewed(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="原理解")
    s = svc.action(s.id, action_type="observe", action_draft="草稿")
    s = svc.record_action(s.id)
    s = svc.feedback(s.id, text="反馈", evidence_type="user_reported", keep=True, basis="暂不改变")
    assert s.status == "reviewed"
    assert s.revision is not None
    assert s.revision.kept is True
    assert s.revision.revised_understanding == "原理解"


def test_feedback_keep_and_revised_mutually_exclusive(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="原理解")
    s = svc.action(s.id, action_type="ask", action_draft="草稿")
    s = svc.record_action(s.id)
    with pytest.raises(ValueError, match="mutually exclusive"):
        svc.feedback(s.id, text="反馈", evidence_type="user_reported", keep=True,
                     revised_understanding="新")


def test_pause_resume_roundtrip(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="理解")
    s = svc.action(s.id, action_type="ask", action_draft="草稿")
    s = svc.pause(s.id)
    assert s.status == "paused"
    assert s.paused_from == "ready"
    s = svc.resume(s.id)
    assert s.status == "ready"
    assert s.paused_from is None


def test_reviewed_cannot_pause(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    s = svc.reflect(s.id, understanding="原理解")
    s = svc.action(s.id, action_type="ask", action_draft="草稿")
    s = svc.record_action(s.id)
    s = svc.feedback(s.id, text="反馈", evidence_type="user_reported",
                     revised_understanding="新")
    assert s.status == "reviewed"
    with pytest.raises(ValueError, match="illegal transition"):
        svc.pause(s.id)


def test_illegal_transitions(tmp_path):
    svc = _svc(tmp_path)
    s = svc.start(situation="s", content_hash="h")
    # record_action before ready
    with pytest.raises(ValueError, match="illegal transition"):
        svc.record_action(s.id)
    # feedback before waiting_feedback
    with pytest.raises(ValueError, match="illegal transition"):
        svc.feedback(s.id, text="x", evidence_type="user_reported")


def test_show_and_list(tmp_path):
    svc = _svc(tmp_path)
    a = svc.start(situation="A", content_hash="h1")
    b = svc.start(situation="B", content_hash="h2")
    assert svc.show(a.id).id == a.id
    with pytest.raises(KeyError):
        svc.show("gs_missing")
    assert {s.id for s in svc.list()} == {a.id, b.id}
    assert {s.id for s in svc.list(status="draft")} == {a.id, b.id}
    assert svc.list(status="reviewed") == []
