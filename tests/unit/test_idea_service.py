"""Unit tests for the idea service."""

from finch.idea.models import AssessIdeaOutput, IdeaAssessment


def test_idea_assessment_round_trips():
    result = IdeaAssessment(
        status="ready",
        core_point="Graph 的价值是恢复与重放",
        matched_evidence_ids=["ev_1"],
        draft_id="draft_idea_abc",
        sample="很多 Agent 项目引入 Graph 只是为了画出来……",
    )
    back = IdeaAssessment.model_validate(result.model_dump(mode="json"))
    assert back == result
    assert back.status == "ready"
    assert back.reason_code is None


def test_assess_output_inherits_idea_assessment_fields():
    out = AssessIdeaOutput(
        status="not_ready",
        reason_code="TOO_BROAD",
        reason="没有具体问题",
        reader_problem=None,
        audience=None,
    )
    assert isinstance(out, IdeaAssessment)
    assert out.reason_code == "TOO_BROAD"
    assert out.sample is None
    assert out.matched_evidence_ids == []
