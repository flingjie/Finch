"""expression_methods 数据模型。"""

from datetime import UTC, datetime

from finch.expression_methods.models import (
    ExpressionMethod,
    MethodPracticeLog,
    MethodSource,
)


def test_expression_method_round_trip():
    now = datetime.now(UTC)
    m = ExpressionMethod(
        id="emethod_1",
        title="用一次具体失败引出问题",
        why_effective="先让读者看到损失",
        when_to_use="经验复盘",
        boundaries="",
        mini_exercise="写一个失败开头",
        purpose_tags=["share_experience"],
        required_material="真实失败记录",
        sources=[
            MethodSource(
                report_id="article_x",
                method_index=2,
                excerpt="",
                source_ref=None,
                why_effective_here="先见损失",
                content_hash="h1",
            )
        ],
        practice_logs=[
            MethodPracticeLog(
                session_id="practice_1",
                verdict="worth_reuse",
                note="清楚多了",
                at=now,
            )
        ],
        created_at=now,
        updated_at=now,
    )
    data = m.model_dump(mode="json")
    assert ExpressionMethod(**data).title == m.title
    assert data["practice_logs"][0]["verdict"] == "worth_reuse"
    assert m.content_fingerprint() == ExpressionMethod(**data).content_fingerprint()


def test_content_fingerprint_stable_and_sensitive():
    now = datetime.now(UTC)
    a = ExpressionMethod(
        id="emethod_a",
        title="t",
        why_effective="w",
        when_to_use="u",
        mini_exercise="e",
        purpose_tags=["explain"],
        created_at=now,
        updated_at=now,
    )
    b = a.model_copy(update={"title": "t2"})
    assert a.content_fingerprint() != b.content_fingerprint()
    assert a.content_fingerprint() == a.model_copy().content_fingerprint()


def test_expression_method_reply_fields_default_and_fingerprint_sensitive():
    now = datetime.now(UTC)
    a = ExpressionMethod(
        id="emethod_r",
        title="t",
        why_effective="w",
        when_to_use="u",
        mini_exercise="e",
        created_at=now,
        updated_at=now,
    )
    assert a.applicable_forms == []
    assert a.reply_usage == ""
    assert a.reply_boundaries == ""
    b = a.model_copy(
        update={"reply_usage": "补一个具体案例", "reply_boundaries": "需要真实经历"}
    )
    assert a.content_fingerprint() != b.content_fingerprint()
