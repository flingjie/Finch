"""Tests for the Critic Suite checker protocol and the first two checkers."""

from types import SimpleNamespace

from finch.content.checkers import (
    CheckContext,
    CheckResult,
    DecisionChecker,
    EvidenceChecker,
)
from finch.content.jobs import AuthorPosition, ContentJob, ContentJobStatus, IntendedEffect
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.last_prompt: str | None = None
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        self.last_prompt = prompt
        return self.ret


def _card(
    cid: str = "ev_1",
    confidence: ClaimConfidence = ClaimConfidence.VERIFIED,
) -> EvidenceCard:
    return EvidenceCard(
        id=cid,
        event_id="e",
        claim="c",
        sources=[],
        confidence=confidence,
        publishable=True,
        topics=[],
    )


def _draft(
    body: str = "hi",
    claims: list[ClaimRef] | None = None,
    job_id: str | None = None,
) -> Draft:
    return Draft(
        id="d",
        kind=DraftKind.REPLY,
        candidate_id="t",
        body=body,
        claims=claims
        if claims is not None
        else [
            ClaimRef(
                statement="x",
                evidence_card_id="ev_1",
                confidence=ClaimConfidence.VERIFIED,
            )
        ],
        content_job_id=job_id,
    )


def _job(decision: str = "Use pool size 10", tradeoff: str = "More memory") -> ContentJob:
    return ContentJob(
        id="job_1",
        source_card_ids=["ev_1"],
        candidate_id=None,
        reader_problem="p",
        audience="engineers",
        intended_effect=IntendedEffect(understand="u"),
        author_position=AuthorPosition(claim="c", decision=decision, tradeoff=tradeoff),
        success_criteria=[],
        recommended_format=DraftKind.REPLY,
        status=ContentJobStatus.READY,
    )


def test_check_result_round_trip():
    cr = CheckResult(
        checker="evidence",
        passed=False,
        severity="hard_fail",
        locations=["claim[0]"],
        issues=["unsupported claim"],
        rewrite_instructions=["remove the unsupported claim"],
        requires_human_input=False,
    )
    back = CheckResult.model_validate(cr.model_dump(mode="json"))
    assert back == cr
    assert back.severity == "hard_fail"
    assert back.requires_human_input is False


def test_check_context_round_trip():
    ctx = CheckContext(draft=_draft(), cards=[_card()])
    assert ctx.job is None
    back = CheckContext.model_validate(ctx.model_dump(mode="json"))
    assert back.draft.id == "d"
    assert back.cards[0].id == "ev_1"


def test_evidence_checker_hard_fails_out_of_set_card():
    checker = EvidenceChecker()
    draft = _draft(
        claims=[
            ClaimRef(
                statement="x",
                evidence_card_id="ev_999",
                confidence=ClaimConfidence.VERIFIED,
            )
        ]
    )
    result = checker.check(CheckContext(draft=draft, cards=[_card("ev_1")]))
    assert result.passed is False
    assert result.severity == "hard_fail"
    assert result.locations
    assert result.issues
    assert result.rewrite_instructions


def test_evidence_checker_hard_fails_non_assertable_confidence():
    checker = EvidenceChecker()
    draft = _draft(
        claims=[
            ClaimRef(
                statement="x",
                evidence_card_id="ev_1",
                confidence=ClaimConfidence.INFERRED,
            )
        ]
    )
    result = checker.check(CheckContext(draft=draft, cards=[_card("ev_1")]))
    assert result.passed is False
    assert result.severity == "hard_fail"


def test_evidence_checker_hard_fails_no_claims():
    checker = EvidenceChecker()
    result = checker.check(CheckContext(draft=_draft(claims=[]), cards=[_card("ev_1")]))
    assert result.passed is False
    assert result.severity == "hard_fail"


def test_evidence_checker_passes_validly_bound_claims():
    checker = EvidenceChecker()
    result = checker.check(CheckContext(draft=_draft(), cards=[_card("ev_1")]))
    assert result.passed is True
    assert result.severity == "low"


def test_evidence_checker_entailment_high_severity():
    checker = EvidenceChecker(FakeRunner(SimpleNamespace(entailment_failed=["x"])))
    result = checker.check(CheckContext(draft=_draft(), cards=[_card("ev_1")]))
    assert result.passed is False
    assert result.severity == "high"
    assert result.locations == ["claim[0]"]


def test_decision_checker_passes_legacy_draft_without_job():
    checker = DecisionChecker()
    result = checker.check(CheckContext(draft=_draft(), cards=[_card("ev_1")]))
    assert result.passed is True


def test_decision_checker_flags_missing_decision_and_tradeoff():
    runner = FakeRunner(
        SimpleNamespace(
            expresses_decision=False,
            expresses_tradeoff=False,
            missing=["decision", "tradeoff"],
        )
    )
    checker = DecisionChecker(runner)
    result = checker.check(
        CheckContext(draft=_draft(body="..."), cards=[_card("ev_1")], job=_job())
    )
    assert result.passed is False
    assert result.severity == "high"
    assert result.requires_human_input is False
    assert result.issues
    assert result.rewrite_instructions
    assert runner.calls == 1


def test_decision_checker_passes_expressed_decision_and_tradeoff():
    runner = FakeRunner(
        SimpleNamespace(expresses_decision=True, expresses_tradeoff=True, missing=[])
    )
    checker = DecisionChecker(runner)
    result = checker.check(
        CheckContext(draft=_draft(), cards=[_card("ev_1")], job=_job())
    )
    assert result.passed is True
    assert result.severity == "low"
