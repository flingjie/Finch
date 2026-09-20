"""轻量推荐反馈：no_time_today 不得映射为长期排斥。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.engagement.metrics import explain_recommendation_adjustments
from finch.engagement.models import RecommendationFeedback


def _fb(value: str, *, dimension: str = "action", i: int = 0) -> RecommendationFeedback:
    return RecommendationFeedback(
        id=f"rfb_{i}",
        opportunity_id="o1",
        snapshot_id="s1",
        dimension=dimension,  # type: ignore[arg-type]
        value=value,
        created_at=datetime.now(UTC),
    )


def test_no_time_today_not_mapped_to_rejection():
    # 多条「今天没时间」不得触发收紧推荐/提高证据门槛的长期排斥建议。
    out = explain_recommendation_adjustments(
        [_fb("no_time_today", i=i) for i in range(3)]
    )
    assert all("raise min evidence" not in o["effect"] for o in out)


def test_no_opening_triggers_rejection_suggestion():
    out = explain_recommendation_adjustments(
        [_fb("no_opening", i=i) for i in range(3)]
    )
    assert any("raise min evidence" in o["effect"] for o in out)


def test_worth_following_outweighs_skip():
    out = explain_recommendation_adjustments(
        [
            _fb("no_opening", i=0),
            _fb("no_opening", i=1),
            _fb("worth_following", dimension="interest", i=2),
            _fb("worth_following", dimension="interest", i=3),
        ]
    )
    assert all("raise min evidence" not in o["effect"] for o in out)
