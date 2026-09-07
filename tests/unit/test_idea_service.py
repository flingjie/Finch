"""Unit tests for the idea service (critic suite + rewrite)."""

from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJob, ContentJobStatus, IntendedEffect
from finch.content.models import Draft, DraftKind
from finch.idea.models import RewriteIdeaOutput
from finch.idea.service import idea_checker_suite, rewrite_idea


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _job():
    return ContentJob(
        id="idea_abc", source_card_ids=[], reader_problem="rp", audience="a",
        intended_effect=IntendedEffect(understand="u"), author_position=None,
        success_criteria=[], recommended_format=DraftKind.ORIGINAL,
        status=ContentJobStatus.CONFIRMED,
    )


def _draft():
    return Draft(
        id="draft_idea_abc", kind=DraftKind.ORIGINAL, language="zh",
        body="旧正文", content_job_id="idea_abc",
    )


def test_idea_checker_suite_drops_evidence():
    suite = idea_checker_suite(runner=None)
    names = [c.name for c in suite]
    assert "evidence" not in names
    assert len(suite) == 7


def test_rewrite_idea_keeps_draft_identity_updates_body():
    job = _job()
    draft = _draft()
    runner = FakeRunner(RewriteIdeaOutput(body="新正文"))
    out = rewrite_idea(
        runner,
        draft,
        [CheckResult(checker="specificity", passed=False, severity="medium",
                     issues=["vague"], rewrite_instructions=["be specific"])],
        job,
    )
    assert runner.calls == 1
    assert out.body == "新正文"
    assert out.id == draft.id
    assert out.claims == []
    assert out.content_job_id == job.id
