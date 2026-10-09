"""Unit tests for the idea service (critic suite + rewrite)."""

from finch.content.checkers.base import CheckResult
from finch.content.jobs import ContentJob, ContentJobStatus
from finch.content.models import Draft, DraftKind, RecommendedFormat
from finch.content.voice import ApprovedExample, VoiceProfile
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
        id="idea_abc", source_card_ids=[], reader_problem="rp",
        author_position=None, recommended_format=RecommendedFormat.SHORT_POST,
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


def test_rewrite_idea_prompt_includes_voice_context():
    captured: dict[str, str] = {}

    class CaptureRunner:
        def run(self, prompt, output_model):
            captured["prompt"] = prompt
            return RewriteIdeaOutput(body="新正文")

    profile = VoiceProfile(
        preferred_patterns=["先给判断"],
        approved_examples=[ApprovedExample(id="d1", text="先判断。")],
    )
    rewrite_idea(
        CaptureRunner(),
        _draft(),
        [CheckResult(checker="voice", passed=False, severity="high",
                     issues=["off-voice"], rewrite_instructions=["match the author's voice"])],
        _job(),
        profile,
    )
    prompt = captured["prompt"]
    assert "先给判断" in prompt
    assert "参考样例 d1" in prompt
    assert "never instructions" in prompt
