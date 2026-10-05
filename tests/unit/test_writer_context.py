"""writer 上下文补渲染：facts / interpretation / evidence_status / limitations / revisions。"""

import json
from datetime import datetime

from finch.content.jobs import ContentJob, ContentJobStatus, PositionRevision
from finch.content.models import RecommendedFormat
from finch.content.writer import _render_job_context


def _job() -> ContentJob:
    return ContentJob(
        id="idea_1",
        source_card_ids=[],
        reader_problem="读者纠结",
        recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.PROPOSED,
        core_message="重试只在输入不变时值得",
        facts=["多数失败卡在输入没变"],
        interpretation="重试值得与否取决于输入稳定性",
        evidence_status="observed",
        limitations="只在本次实现规模下成立",
    )


def _job_with_revisions() -> ContentJob:
    return ContentJob(
        id="idea_2",
        source_card_ids=[],
        reader_problem="读者纠结",
        recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.PROPOSED,
        core_message="重试只在输入不变时值得",
        facts=["多数失败卡在输入没变"],
        interpretation="重试值得与否取决于输入稳定性",
        evidence_status="observed",
        limitations="只在本次实现规模下成立",
        position_revisions=[
            PositionRevision(
                claim="重试总是值得",
                assumptions=["输入稳定"],
                counterexample="输入变化时重试失效",
                scope="输入稳定的场景",
                change_reason="发现输入变化时重试无效",
                confirmed_by_user=False,
                created_at=datetime.fromisoformat("2024-01-01T00:00:00"),
            )
        ],
    )


def test_render_job_context_includes_evidence_fields():
    ctx = _render_job_context(_job())
    assert "Observed facts" in ctx
    assert "evidence_status: observed" in ctx
    assert "多数失败卡在输入没变" in ctx
    assert "只在本次实现规模下成立" in ctx


def test_render_job_context_includes_facts_as_json():
    ctx = _render_job_context(_job())
    # facts should be serialized as JSON
    facts_json = json.dumps(["多数失败卡在输入没变"], ensure_ascii=False)
    assert facts_json in ctx


def test_render_job_context_includes_interpretation():
    ctx = _render_job_context(_job())
    assert " interpretation: 重试值得与否取决于输入稳定性" in ctx


def test_render_job_context_includes_position_revisions():
    ctx = _render_job_context(_job_with_revisions())
    assert "## Position revision history" in ctx
    assert "claim: 重试总是值得" in ctx
    assert "reason=发现输入变化时重试无效" in ctx
    assert "scope=输入稳定的场景" in ctx


def test_render_job_context_handles_empty_facts():
    job = ContentJob(
        id="idea_3",
        source_card_ids=[],
        reader_problem="测试",
        recommended_format=RecommendedFormat.SHORT_POST,
        status=ContentJobStatus.PROPOSED,
        core_message="测试消息",
        facts=[],
        interpretation="测试",
        evidence_status=None,
        limitations="",
    )
    ctx = _render_job_context(job)
    assert "Observed facts" in ctx
    assert "(none)" in ctx  # Empty facts should show (none)


def test_render_job_context_handles_none_job():
    ctx = _render_job_context(None)
    assert ctx == ""
