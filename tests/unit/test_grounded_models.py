"""GroundedSession 模型与 id 助手单元测试。"""

from datetime import UTC, datetime

from finch.grounded.models import (
    Constraint,
    GroundedSession,
    feedback_id_for,
    grounded_session_id_for,
)


def _session(**overrides) -> GroundedSession:
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    data = {
        "id": "gs_test",
        "situation": "situation",
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return GroundedSession(**data)


def test_session_id_content_addressed_and_request_precedence():
    a = grounded_session_id_for(content_hash="h1")
    b = grounded_session_id_for(content_hash="h1")
    assert a == b
    assert a.startswith("gs_")
    assert a != grounded_session_id_for(content_hash="h2")
    # request_id 优先，覆盖 content_hash
    assert grounded_session_id_for(content_hash="h1", request_id="r1") != a
    assert grounded_session_id_for(content_hash="h1", request_id="r1") == grounded_session_id_for(
        content_hash="h2", request_id="r1"
    )


def test_feedback_id_includes_evidence_type():
    t = "same text"
    assert feedback_id_for(text=t, evidence_type="source_quote") == feedback_id_for(
        text=t, evidence_type="source_quote"
    )
    assert feedback_id_for(text=t, evidence_type="source_quote") != feedback_id_for(
        text=t, evidence_type="ai_inferred"
    )
    assert feedback_id_for(text=t, evidence_type="source_quote") != feedback_id_for(
        text="different", evidence_type="source_quote"
    )


def test_default_fields():
    s = _session()
    assert s.schema_version == 1
    assert s.status == "draft"
    assert s.constraints == []
    assert s.feedback == []
    assert s.revision is None
    assert s.person_ref is None
    assert s.action_type is None
    assert s.action_recorded is False


def test_old_yaml_missing_new_fields_reads():
    # 只有核心字段（模拟未来/旧 YAML 缺新字段）也能 model_validate。
    now = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    s = GroundedSession.model_validate(
        {"id": "gs_old", "situation": "x", "created_at": now, "updated_at": now}
    )
    assert s.schema_version == 1
    assert s.coverage == []
    assert s.paused_from is None


def test_constraint_evidence_defaults_unknown():
    c = Constraint(description="时间紧张")
    assert c.evidence == "unknown"
    assert c.basis == ""
