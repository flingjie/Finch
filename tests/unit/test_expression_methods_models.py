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
        sources=[
            MethodSource(
                report_id="article_x",
                method_index=2,
                excerpt="",
                source_ref=None,
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
