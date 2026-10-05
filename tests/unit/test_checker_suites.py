"""Tests for content-type-adaptive checker suites (内容类型自适应检查器套件).

规范 §7.2/§7.3：按内容类型选择检查器组合，去掉「强制绑定个人项目」的 PortabilityChecker
与「强制 decision/tradeoff」的 DecisionChecker，改为类型专属检查。本文件覆盖：

- ``content_type_for`` 从 ContentJob 推导内容类型；
- ``checker_suite_for`` 按类型选择组合（含不变量 hard-fail 检查器）；
- 新的 ``MethodCardChecker``（方法卡完整性：输入/步骤/输出/限制）。
"""

from finch.content.checkers.base import CheckContext
from finch.content.checkers.method_card import MethodCardChecker
from finch.content.checkers.suites import checker_suite_for, content_type_for
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus
from finch.content.models import ContentType, Draft, DraftKind, RecommendedFormat


def _job(
    *,
    fmt: RecommendedFormat = RecommendedFormat.SHORT_POST,
    intent: str = "stance",
    content_type: ContentType | None = None,
) -> ContentJob:
    return ContentJob(
        id="job_1",
        source_card_ids=[],
        candidate_id=None,
        reader_problem="p",
        author_position=AuthorPosition(claim="c", decision="d", tradeoff="t"),
        recommended_format=fmt,
        status=ContentJobStatus.CONFIRMED,
        intent=intent,  # type: ignore[arg-type]
        content_type=content_type,
    )


def _draft(body: str) -> Draft:
    return Draft(id="d", kind=DraftKind.ORIGINAL, language="zh", body=body, claims=[])


# ---- content_type_for ----

def test_content_type_for_discussion_reply_from_response_formats():
    for fmt in (RecommendedFormat.REPLY, RecommendedFormat.QUOTE, RecommendedFormat.DM):
        assert content_type_for(_job(fmt=fmt)) == ContentType.DISCUSSION_REPLY


def test_content_type_for_exploration_from_intent():
    assert (
        content_type_for(_job(fmt=RecommendedFormat.THREAD, intent="exploration"))
        == ContentType.EXPLORATION_HYPOTHESIS
    )


def test_content_type_for_defaults_to_concept_explanation():
    assert content_type_for(_job()) == ContentType.CONCEPT_EXPLANATION


def test_content_type_for_respects_explicit_override():
    assert (
        content_type_for(_job(content_type=ContentType.METHOD_CARD))
        == ContentType.METHOD_CARD
    )
    assert (
        content_type_for(_job(fmt=RecommendedFormat.REPLY, content_type=ContentType.METHOD_CARD))
        == ContentType.METHOD_CARD
    )


# ---- checker_suite_for ----

def _names(content_type: ContentType) -> list[str]:
    return [c.name for c in checker_suite_for(content_type)]


def test_every_suite_drops_portability_and_decision():
    for ct in ContentType:
        names = _names(ct)
        assert "portability" not in names
        assert "decision" not in names


def test_every_suite_keeps_safety_hard_fail():
    for ct in ContentType:
        assert "safety" in _names(ct)


def test_concept_explanation_suite_is_safety_specificity_structure():
    assert _names(ContentType.CONCEPT_EXPLANATION) == [
        "safety",
        "voice",
        "specificity",
        "structure",
    ]


def test_discussion_reply_suite_includes_responsiveness():
    assert "responsiveness" in _names(ContentType.DISCUSSION_REPLY)
    assert "structure" not in _names(ContentType.DISCUSSION_REPLY)


def test_method_card_suite_includes_method_card_checker():
    assert "method_card" in _names(ContentType.METHOD_CARD)


# ---- MethodCardChecker ----

_COMPLETE_CARD = """\
适用处境：团队里同一个故障反复出现，需要把它固化成可回放的用例。

输入：一段完整 trace、出错的代码路径、失败时的环境变量。

步骤：
1. 用 trace 还原出最小触发输入。
2. 固定时间相关的状态。
3. 断言哪些结果必须一致、哪些允许浮动。

输出与判断：得到一条可重跑的用例；重跑结果稳定即说明回放有效。

限制：只覆盖「时间依赖」这一种失败来源，对并发竞态不适用。
"""


def test_method_card_checker_passes_complete_card():
    result = MethodCardChecker().check(CheckContext(draft=_draft(_COMPLETE_CARD), cards=[]))
    assert result.passed is True
    assert result.severity == "low"


def test_method_card_checker_flags_missing_limits():
    body = _COMPLETE_CARD.split("限制：")[0]  # 去掉「限制」段落
    result = MethodCardChecker().check(CheckContext(draft=_draft(body), cards=[]))
    assert result.passed is False
    assert result.severity == "medium"
    assert any("限制" in i for i in result.issues)
    assert result.rewrite_instructions


def test_method_card_checker_flags_multiple_missing_sections():
    result = MethodCardChecker().check(
        CheckContext(draft=_draft("把故障固化成可回放的用例。"), cards=[])
    )
    assert result.passed is False
    missing = " ".join(result.issues)
    assert "输入" in missing
    assert "步骤" in missing
    assert "输出" in missing
    assert "限制" in missing


def test_judgment_shift_uses_structure_checker():
    names = [c.__class__.__name__ for c in checker_suite_for(ContentType.JUDGMENT_SHIFT)]
    assert "StructureChecker" in names
    assert "SafetyChecker" in names
