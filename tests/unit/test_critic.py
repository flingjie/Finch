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
