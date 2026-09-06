from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.gate.models import InputAction, InputRequest, ProposedPosition
from finch.gate.render import (
    render_compact_resolve,
    render_confirm_card,
    render_daily_summary,
    render_evidence,
    render_outcome,
    render_position_diff,
    render_produced,
    state_label,
)


def _request():
    return InputRequest(
        run_id="r1",
        job_id="j1",
        topic="topic here",
        why_now="why now here",
        proposed_position=ProposedPosition(claim="c", decision="d", tradeoff="t"),
        questions=["q1"],
    )


def test_render_confirm_card_contains_sections():
    text = render_confirm_card(_request(), [])
    assert "topic here" in text
    assert "主张：c" in text
    assert "方案：d" in text
    assert "取舍：t" in text
    assert "q1" in text
    assert "依据：0 张证据卡" in text


def test_render_confirm_card_empty_position():
    text = render_confirm_card(
        _request().model_copy(update={"proposed_position": ProposedPosition()}), []
    )
    assert "主张：(未填)" in text


def test_state_label_mapping():
    assert state_label("COMPLETED") == "已完成"
    assert state_label("NEEDS_INPUT") == "等待你的确认"
    assert state_label("SKIPPED") == "已跳过"
    assert state_label("FAILED") == "运行失败"
    assert state_label("BLOCKED") == "运行失败"
    assert state_label("WEIRD_STATE") == "WEIRD_STATE"


def test_render_daily_summary_with_and_without_engagement():
    full = render_daily_summary(posts_found=30, engagement_drafts=3, pending_original=1)
    assert "✓ 今日分析已完成" in full
    assert "扫描 30 条帖子" in full
    assert "生成 3 条互动草稿" in full
    assert "1 个值得展开的主题" in full
    no_eng = render_daily_summary(posts_found=None, engagement_drafts=0, pending_original=1)
    assert "互动内容" not in no_eng
    assert "1 个值得展开的主题" in no_eng


def test_render_compact_resolve_recommends_confirm():
    text = render_compact_resolve(_request(), engagement_drafts=3)
    assert "Daily 分析完成" in text
    assert "uv run finch run resolve --confirm" in text
    assert "另外有 3 条互动草稿" in text


def test_render_outcome_and_produced():
    assert render_outcome(InputAction.CONFIRM) == "✓ 已确认你的立场"
    assert render_outcome(InputAction.EDIT) == "✓ 已更新你的立场"
    assert render_outcome(InputAction.SKIP) == "✓ 已跳过这个主题"
    assert render_outcome(InputAction.STOP) == "✓ 已保存并退出"
    assert render_outcome(None) == "✓ 已保存进度并退出"
    produced = render_produced(engagement_drafts=3, original_drafts=1)
    assert "互动草稿：3 条" in produced
    assert "原创草稿：1 条" in produced


def test_render_evidence():
    cards = [EvidenceCard(
        id="ev1", event_id="e", claim="the claim", sources=[],
        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=["t"],
    )]
    assert "the claim" in render_evidence(cards)
    assert "无证据" in render_evidence([])


def test_render_position_diff_shows_change():
    before = ProposedPosition(claim="c", decision="d", tradeoff="t")
    after = ProposedPosition(claim="c2", decision="d", tradeoff="t")
    diff = render_position_diff(before, after)
    assert "-claim: c" in diff
    assert "+claim: c2" in diff
