from finch.problem_discovery.models import Admission, Problem


def test_problem_default_admission_is_quarantined_when_no_evidence_on_construct():
    # Fresh construct without going through admit_problem: leave default
    # as quarantined so accidental writes are safe.
    p = Problem(problem_id="prob_x", title="t")
    assert p.admission == Admission.QUARANTINED
    assert p.admission_reason == ""


def test_legacy_dict_without_admission_infers_from_evidence():
    raw = {
        "problem_id": "prob_legacy",
        "title": "legacy",
        "evidence": [
            {
                "source_ref": "s1",
                "quote": "q",
                "claim": "c",
            }
        ],
    }
    p = Problem.model_validate(raw)
    assert p.admission == Admission.VERIFIED


def test_legacy_dict_without_admission_empty_evidence_quarantines():
    p = Problem.model_validate({"problem_id": "prob_y", "title": "y", "evidence": []})
    assert p.admission == Admission.QUARANTINED
