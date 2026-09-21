"""轻量推荐反馈：no_time_today 不得映射为长期排斥；prepare 信号正确、不重复计数。"""

from __future__ import annotations

from datetime import UTC, datetime

from finch.engagement.metrics import explain_recommendation_adjustments
from finch.engagement.models import (
    ActionFeedbackValue,
    InterestFeedbackValue,
    RecommendationFeedback,
)


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
        [_fb(ActionFeedbackValue.NO_TIME_TODAY.value, i=i) for i in range(3)]
    )
    assert all("raise min evidence" not in o["effect"] for o in out)


def test_no_opening_triggers_rejection_suggestion():
    out = explain_recommendation_adjustments(
        [_fb(ActionFeedbackValue.NO_OPENING.value, i=i) for i in range(3)]
    )
    assert any("raise min evidence" in o["effect"] for o in out)


def test_worth_following_outweighs_skip():
    out = explain_recommendation_adjustments(
        [
            _fb(ActionFeedbackValue.NO_OPENING.value, i=0),
            _fb(ActionFeedbackValue.NO_OPENING.value, i=1),
            _fb(InterestFeedbackValue.WORTH_FOLLOWING.value, dimension="interest", i=2),
            _fb(InterestFeedbackValue.WORTH_FOLLOWING.value, dimension="interest", i=3),
        ]
    )
    assert all("raise min evidence" not in o["effect"] for o in out)


def test_prepare_signal_is_labeled_prepared_not_replied():
    # prepare 不再与 accept 重复计数，信号键为 prepared（非 replied）。
    out = explain_recommendation_adjustments(
        [_fb(ActionFeedbackValue.PREPARE.value, i=i) for i in range(2)]
    )
    signals = {a["signal"] for a in out}
    assert any(s.startswith("prepared=") for s in signals)
    assert not any("replied" in s for s in signals)


def test_prepare_does_not_tighten_when_not_skipping():
    # 2×no_opening（skip）+ 2×prepare：skip 未达阈值 3，不得给出「收紧」建议。
    out = explain_recommendation_adjustments(
        [
            _fb(ActionFeedbackValue.NO_OPENING.value, i=0),
            _fb(ActionFeedbackValue.NO_OPENING.value, i=1),
            _fb(ActionFeedbackValue.PREPARE.value, i=2),
            _fb(ActionFeedbackValue.PREPARE.value, i=3),
        ]
    )
    assert all("raise min evidence" not in o["effect"] for o in out)
    assert any("boost connection_opportunity" in o["effect"] for o in out)
