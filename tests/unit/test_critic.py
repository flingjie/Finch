from finch.content.checkers import CheckResult
from finch.content.critic import CritiqueResult, critique, evaluate_passed
from finch.content.models import ClaimRef, Draft, DraftKind
from finch.evidence.models import ClaimConfidence, EvidenceCard
from finch.settings import QualityGates


class FakeRunner:
    def __init__(self, ret):
        self.calls = 0
        self.ret = ret

    def run(self, prompt, output_model, **kw):
        self.calls += 1
        return self.ret


def _draft():
    return Draft(
        id="d",
        kind=DraftKind.REPLY,
        candidate_id="t",
        body="hi",
        claims=[
            ClaimRef(
                statement="x",
                evidence_card_id="ev_1",
                confidence=ClaimConfidence.VERIFIED,
            )
        ],
    )


def test_evaluate_passed_gates():
    ok = CritiqueResult(passed=False, quality_score=0.8)
    assert evaluate_passed(ok, QualityGates()) is True
    low = CritiqueResult(passed=False, quality_score=0.5)
    assert evaluate_passed(low, QualityGates()) is False
    inv = CritiqueResult(passed=False, quality_score=0.9, invented_personal_experience=True)
    assert evaluate_passed(inv, QualityGates()) is False
    ent = CritiqueResult(passed=False, quality_score=0.9, entailment_failed=["x"])
    assert evaluate_passed(ent, QualityGates()) is False


def test_critique_calls_runner_once():
    r = FakeRunner(CritiqueResult(passed=True, quality_score=0.8))
    out = critique(
        r,
        _draft(),
        {
            "ev_1": EvidenceCard(
                id="ev_1",
                event_id="e",
                claim="c",
                sources=[],
                confidence=ClaimConfidence.VERIFIED,
                publishable=True,
                topics=[],
            )
        },
    )
    assert r.calls == 1 and out.quality_score == 0.8


def test_critique_result_round_trips_checks():
    result = CritiqueResult(
        passed=True,
        quality_score=0.8,
        checks=[CheckResult(checker="evidence", passed=True, severity="low")],
    )
    back = CritiqueResult.model_validate(result.model_dump(mode="json"))
    assert back.checks == result.checks
    assert back.checks[0].severity == "low"


def test_critique_result_from_checks_computes_passed_from_aggregate():
    failed = [
        CheckResult(
            checker="specificity",
            passed=False,
            severity="medium",
            locations=["sentence[0]"],
            issues=["vague"],
            rewrite_instructions=["be specific"],
        )
    ]
    result = CritiqueResult.from_checks(failed)
    assert result.passed is False
    assert result.checks == failed

    hard = CheckResult(checker="evidence", passed=False, severity="hard_fail")
    assert CritiqueResult.from_checks([hard]).passed is False

    passed = [CheckResult(checker="evidence", passed=True, severity="low")]
    assert CritiqueResult.from_checks(passed).passed is True
