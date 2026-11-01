from finch.problem_discovery.discover import admit_problem
from finch.problem_discovery.models import Admission, Problem, ProblemEvidence


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


def test_admit_problem_quarantines_without_evidence():
    p = Problem(problem_id="prob_a", title="a", evidence=[])
    out = admit_problem(p)
    assert out.admission == Admission.QUARANTINED
    assert out.admission_reason == "no_valid_evidence"


def test_admit_problem_verifies_with_evidence():
    p = Problem(
        problem_id="prob_b",
        title="b",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    out = admit_problem(p)
    assert out.admission == Admission.VERIFIED
    assert out.admission_reason == "has_valid_evidence"


def test_admit_problem_force_draft():
    p = Problem(
        problem_id="prob_c",
        title="c",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    out = admit_problem(p, force_draft=True)
    assert out.admission == Admission.DRAFT
    assert out.admission_reason == "host_draft"
