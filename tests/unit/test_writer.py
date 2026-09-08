from finch.codex.runner import CodexRunner
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
)
from finch.content.models import ClaimRef, Draft, DraftKind, RecommendedFormat
from finch.content.writer import rewrite
from finch.evidence.models import ClaimConfidence, EvidenceCard


class FakeRunner(CodexRunner):
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _card():
    return EvidenceCard(id="ev_1", event_id="e", claim="rate limit", sources=[],
                        confidence=ClaimConfidence.VERIFIED, publishable=True, topics=[])


def _good_draft():
    return Draft(id="d", kind=DraftKind.REPLY, candidate_id="t1", language="en",
                 body="hi", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                             confidence=ClaimConfidence.VERIFIED)])


def _job():
    return ContentJob(
        id="job_1",
        source_card_ids=["ev_1"],
        candidate_id="t1",
        reader_problem="readers don't know how to rate limit",
        author_position=AuthorPosition(
            claim="use token bucket",
            decision="use token bucket",
            tradeoff="more memory",
            change_mind_if="if a library already covers it",
        ),
        recommended_format=RecommendedFormat.REPLY,
        status=ContentJobStatus.CONFIRMED,
        core_message="use a token bucket for rate limiting",
        why_now="we hit rate limits this week",
    )


def _failed_check(
    checker: str = "specificity",
    issue: str = "too vague",
    instruction: str = "replace the vague sentence with a specific claim",
) -> CheckResult:
    return CheckResult(
        checker=checker,
        passed=False,
        severity="medium",
        locations=["sentence[0]"],
        issues=[issue],
        rewrite_instructions=[instruction],
    )


def test_rewrite_regenerates_body():
    out = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t1", language="en",
                body="fixed", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                               confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(out)
    d = rewrite(r, _good_draft(), [_failed_check()], {"ev_1": _card()})
    assert d is not None and d.body == "fixed"


def test_rewrite_prompt_contains_only_failed_instructions():
    captured: list[str] = []

    class CaptureRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            captured.append(prompt)
            return _good_draft().model_copy(update={"body": "fixed"})

    rewrite(
        CaptureRunner(),
        _good_draft(),
        [_failed_check(checker="specificity", instruction="tie the claim to evidence")],
        {"ev_1": _card()},
    )
    prompt = captured[0]
    assert "tie the claim to evidence" in prompt
    assert "specificity" in prompt
    assert "too vague" in prompt
    assert "Do NOT restyle" in prompt
    assert "improve the writing" not in prompt


def test_rewrite_renders_each_failed_check():
    captured: list[str] = []

    class CaptureRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            captured.append(prompt)
            return _good_draft().model_copy(update={"body": "fixed"})

    rewrite(
        CaptureRunner(),
        _good_draft(),
        [
            _failed_check(checker="specificity", instruction="fix specificity"),
            _failed_check(checker="portability", instruction="anchor the claim"),
        ],
        {"ev_1": _card()},
    )
    prompt = captured[0]
    assert "fix specificity" in prompt
    assert "anchor the claim" in prompt
    assert "specificity" in prompt
    assert "portability" in prompt


def test_rewrite_preserves_stamped_identity():
    out = Draft(id="wrong", kind=DraftKind.ORIGINAL, candidate_id=None, language="zh",
                body="fixed", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                               confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(out)
    d = rewrite(r, _good_draft(), [_failed_check()], {"ev_1": _card()})
    assert d is not None
    assert d.id == "d"
    assert d.kind == DraftKind.REPLY
    assert d.candidate_id == "t1"
    assert d.language == "en"


def test_rewrite_prompt_includes_job_context():
    captured: list[str] = []

    class CaptureRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            captured.append(prompt)
            return _good_draft().model_copy(update={"body": "fixed"})

    rewrite(
        CaptureRunner(),
        _good_draft(),
        [_failed_check()],
        {"ev_1": _card()},
        _job(),
    )
    prompt = captured[0]
    assert "use token bucket" in prompt
    assert "more memory" in prompt
    assert "use a token bucket for rate limiting" in prompt
    assert "Author's decision and intent" in prompt


def test_rewrite_without_job_omits_job_context():
    captured: list[str] = []

    class CaptureRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            captured.append(prompt)
            return _good_draft().model_copy(update={"body": "fixed"})

    rewrite(CaptureRunner(), _good_draft(), [_failed_check()], {"ev_1": _card()})
    prompt = captured[0]
    assert "Author's decision and intent" not in prompt


def test_rewrite_downgrades_model_user_confirmed():
    out = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t1", language="en",
                body="fixed", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                               confidence=ClaimConfidence.USER_CONFIRMED)])
    d = rewrite(FakeRunner(out), _good_draft(), [_failed_check()], {"ev_1": _card()})
    assert d.claims[0].confidence == ClaimConfidence.SUPPORTED


def test_rewrite_preserves_run_id(monkeypatch):
    from finch.content import writer

    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", claims=[], run_id="r1")

    class _Runner:
        def run(self, prompt, model):
            return Draft(id="d1", kind=DraftKind.ORIGINAL, body="after", claims=[])

    monkeypatch.setattr(writer, "_sanitize_draft_claims", lambda d: d)
    out = writer.rewrite(_Runner(), draft, [], {})
    assert out.run_id == "r1"
    assert out.body == "after"


def test_rewrite_with_instruction_uses_nl_instruction(monkeypatch):
    from finch.content import writer

    captured: dict[str, str] = {}

    class _Runner:
        def run(self, prompt, model):
            captured["prompt"] = prompt
            return Draft(id="d1", kind=DraftKind.ORIGINAL, body="revised", claims=[])

    monkeypatch.setattr(writer, "_sanitize_draft_claims", lambda d: d)
    draft = Draft(id="d1", kind=DraftKind.ORIGINAL, body="before", claims=[], run_id="r1")
    out = writer.rewrite_with_instruction(_Runner(), draft, "语气弱一点", {})
    assert out.body == "revised"
    assert "语气弱一点" in captured["prompt"]


def test_draft_from_job_prompt_contains_scoping_rules():
    from pathlib import Path

    text = Path("prompts/draft-from-job.md").read_text()
    assert "条件结论" in text
    assert "不追加" in text and "免责声明" in text
    assert "至多出现一次" in text
