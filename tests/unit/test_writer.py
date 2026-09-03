from finch.codex.runner import CodexRunner
from finch.content.checkers.base import CheckResult
from finch.content.jobs import (
    AuthorPosition,
    ContentJob,
    ContentJobStatus,
    IntendedEffect,
    SuccessCriterion,
)
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.content.writer import rewrite, write_original, write_reply
from finch.evidence.models import ClaimConfidence, EvidenceCard, JudgeScores, MatchResult
from finch.twitter.models import DiscussionCandidate


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


def _match():
    return MatchResult(candidate_id="t1", card_ids=["ev_1"],
                       scores=JudgeScores(relevance=0.9, evidence_strength=0.9,
                                          incremental_value=0.9, discussability=0.9),
                       timing=1.0, relationship_value=0.5, score=0.9)


def _candidate():
    return DiscussionCandidate(id="t1", author_handle="u", text="t",
                               url="https://x.com/u/status/1")


def _job():
    return ContentJob(
        id="job_1",
        source_card_ids=["ev_1"],
        candidate_id="t1",
        reader_problem="readers don't know how to rate limit",
        audience="backend engineers",
        intended_effect=IntendedEffect(understand="token bucket rate limiting"),
        author_position=AuthorPosition(
            claim="use token bucket",
            decision="use token bucket",
            tradeoff="more memory",
            confirmed=True,
        ),
        success_criteria=[
            SuccessCriterion(id="c1", description="critic passes", measurement="critic")
        ],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.READY,
    )


def test_write_reply_returns_draft():
    r = FakeRunner(_good_draft())
    d = write_reply(r, _match(), _candidate(), {"ev_1": _card()})
    assert d is not None and d.id == "d"


def test_write_reply_none_on_invalid_claim():
    bad = _good_draft().model_copy(update={"claims": [
        ClaimRef(statement="x", evidence_card_id="ev_999", confidence=ClaimConfidence.VERIFIED)]})
    r = FakeRunner(bad)
    assert write_reply(r, _match(), _candidate(), {"ev_1": _card()}) is None


def test_write_reply_stamps_metadata():
    # 模型返回的 Draft 缺 candidate_id —— write_reply 必须盖章，否则 reply 被误判为 original
    unstamped = Draft(id="d", kind=DraftKind.REPLY, body="hi",
                      claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                       confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(unstamped)
    d = write_reply(r, _match(), _candidate(), {"ev_1": _card()})
    assert d is not None
    assert d.candidate_id == "t1"
    assert d.kind == DraftKind.REPLY
    assert d.language == "en"


def test_write_reply_job_path_stamps_metadata():
    # match=None (job-based reply) must still stamp job id + position statement
    unstamped = Draft(id="d", kind=DraftKind.REPLY, body="hi",
                      claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                       confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(unstamped)
    d = write_reply(r, None, _candidate(), {"ev_1": _card()}, _job())
    assert d is not None
    assert d.candidate_id == "t1"
    assert d.content_job_id == "job_1"
    assert d.position_statement == "use token bucket"


def test_write_reply_job_path_none_on_invalid_claim():
    bad = _good_draft().model_copy(update={"claims": [
        ClaimRef(statement="x", evidence_card_id="ev_999", confidence=ClaimConfidence.VERIFIED)]})
    r = FakeRunner(bad)
    assert write_reply(r, None, _candidate(), {"ev_1": _card()}, _job()) is None


def test_write_reply_prompt_orders_instructions_before_candidate_data():
    captured: list[str] = []

    class CaptureRunner(CodexRunner):
        def run(self, prompt, output_model, **kw):
            captured.append(prompt)
            return _good_draft()

    write_reply(CaptureRunner(), _match(), _candidate(), {"ev_1": _card()})
    prompt = captured[0]
    assert "Instructions:" in prompt
    assert "## Untrusted candidate data" in prompt
    assert prompt.index("Instructions:") < prompt.index("## Untrusted candidate data")


def test_write_original_returns_draft():
    good = Draft(id="d", kind=DraftKind.ORIGINAL, candidate_id=None, language="zh",
                 body="日记", claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                               confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(good)
    d = write_original(r, [_card()])
    assert d is not None and d.id == "d" and d.kind == DraftKind.ORIGINAL


def test_write_original_none_on_invalid_claim():
    bad = Draft(id="d", kind=DraftKind.ORIGINAL, candidate_id=None, language="zh",
                body="日记", claims=[ClaimRef(statement="x", evidence_card_id="ev_999",
                                              confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(bad)
    assert write_original(r, [_card()]) is None


def test_write_original_stamps_metadata():
    # 模型返回的 Draft 元数据错误 —— write_original 必须覆盖为 original/None/zh
    wrong = Draft(id="d", kind=DraftKind.REPLY, candidate_id="t1", body="日记",
                  claims=[ClaimRef(statement="x", evidence_card_id="ev_1",
                                   confidence=ClaimConfidence.VERIFIED)])
    r = FakeRunner(wrong)
    d = write_original(r, [_card()])
    assert d is not None
    assert d.kind == DraftKind.ORIGINAL
    assert d.candidate_id is None
    assert d.language == "zh"


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
