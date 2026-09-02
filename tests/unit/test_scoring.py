from datetime import UTC, datetime

from finch.evidence.models import JudgeScores, MatchResult
from finch.evidence.scoring import apply_gates, formula_score, relationship_value, timing_value
from finch.settings import QualityGates


def test_formula_weights():
    s = JudgeScores(relevance=1, evidence_strength=1, incremental_value=1, discussability=0)
    assert formula_score(s, timing=1, relationship_value=1) == 1.0
    assert formula_score(s, timing=0, relationship_value=0) == 0.8  # discussability 不进公式


def test_timing_missing_uses_default():
    assert timing_value(None, 0.3) == 0.3
    assert timing_value(datetime(2026, 1, 1, tzinfo=UTC), 0.3) == 1.0


def test_relationship_lists():
    assert relationship_value("Ada", high_value_authors=["@ada"], blocked_authors=[]) == 1.0
    assert relationship_value("@bob", high_value_authors=[], blocked_authors=["bob"]) == 0.0
    assert relationship_value("carol", high_value_authors=[], blocked_authors=[]) == 0.5


def test_apply_gates_filters_and_truncates():
    gates = QualityGates(max_daily_replies=1, min_candidate_score=0.65,
                         min_evidence_score=0.75, min_discussability=0.50)
    def mr(cid, score, evid, disc):
        return MatchResult(
            candidate_id=cid, card_ids=["ev"],
            scores=JudgeScores(relevance=1, evidence_strength=evid,
                               incremental_value=1, discussability=disc),
            timing=1, relationship_value=0.5, score=score,
        )
    kept = apply_gates([
        mr("low", 0.5, 0.9, 0.9),
        mr("weakev", 0.9, 0.2, 0.9),
        mr("quiet", 0.9, 0.9, 0.1),
        mr("best", 0.91, 0.9, 0.9),
        mr("ok", 0.80, 0.9, 0.9),
    ], gates)
    assert [m.candidate_id for m in kept] == ["best"]
