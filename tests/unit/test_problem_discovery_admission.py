from datetime import UTC, datetime

from pydantic import BaseModel

from finch.problem_discovery.discover import (
    ProblemDraft,
    ProblemEvidenceDraft,
    ProblemsExtraction,
    admit_problem,
    build_problem,
    discover_problems,
)
from finch.problem_discovery.models import Admission, EvidenceLevel, Problem, ProblemEvidence


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
    # Explicit wrong admission survives _legacy_admission; admit_problem must overwrite it.
    p = Problem(
        problem_id="prob_a",
        title="a",
        evidence=[],
        admission=Admission.VERIFIED,
        admission_reason="wrong",
    )
    assert p.admission == Admission.VERIFIED
    assert p.admission_reason == "wrong"
    out = admit_problem(p)
    assert out is p
    assert p.admission == Admission.QUARANTINED
    assert p.admission_reason == "no_valid_evidence"


def test_admit_problem_verifies_with_evidence():
    p = Problem(
        problem_id="prob_b",
        title="b",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
        admission=Admission.QUARANTINED,
        admission_reason="wrong",
    )
    assert p.admission == Admission.QUARANTINED
    assert p.admission_reason == "wrong"
    out = admit_problem(p)
    assert out is p
    assert p.admission == Admission.VERIFIED
    assert p.admission_reason == "has_valid_evidence"


def test_admit_problem_force_draft():
    p = Problem(
        problem_id="prob_c",
        title="c",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    out = admit_problem(p, force_draft=True)
    assert out.admission == Admission.DRAFT
    assert out.admission_reason == "host_draft"


def _now():
    return datetime(2026, 11, 1, tzinfo=UTC)


def test_n05_same_author_id_two_refs_caps_e1():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1",
                quote="alpha",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
            ProblemEvidenceDraft(
                source_ref="s2",
                quote="beta",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert p is not None
    assert all(e.evidence_level == EvidenceLevel.E1 for e in p.evidence)


def test_n05_empty_authors_two_refs_caps_e1():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(source_ref="s1", quote="alpha", evidence_level="e2"),
            ProblemEvidenceDraft(source_ref="s2", quote="beta", evidence_level="e2"),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert p is not None
    assert all(e.evidence_level == EvidenceLevel.E1 for e in p.evidence)


def test_n05_two_stated_author_ids_allow_e2():
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1",
                quote="alpha",
                evidence_level="e2",
                author_id="alice",
                author_identity_confidence="stated",
            ),
            ProblemEvidenceDraft(
                source_ref="s2",
                quote="beta",
                evidence_level="e2",
                author_id="bob",
                author_identity_confidence="stated",
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        now=_now(),
    )
    assert p is not None
    assert {e.evidence_level for e in p.evidence} == {EvidenceLevel.E2}


# --- validate_upsert / discover_problems integration ---

import pytest  # noqa: E402
from test_problem_discovery_discover import _art, _discover_kwargs  # noqa: E402

from finch.problem_discovery.importing import import_discussion  # noqa: E402
from finch.problem_discovery.repository import (  # noqa: E402
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.problem_discovery.shadow import validate_upsert  # noqa: E402
from finch.sources.store import ArtifactRepository  # noqa: E402
from finch.storage.workspace import Workspace  # noqa: E402

_BODY = "Real body about session restore failure."


def _setup(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    refs = import_discussion(
        ws, url="https://example.com/x", text=_BODY, title="seed", author="u"
    )
    return ws, refs[0]


def _run(ws, ref, quote, **kw):
    drafts = {
        "problems": [
            {
                "title": "Session restore",
                "evidence": [{"source_ref": ref, "quote": quote, "claim": "c"}],
            }
        ]
    }
    return validate_upsert(
        drafts,
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
        **kw,
    )


def test_validate_upsert_quarantines_invented_quote(tmp_path):
    ws, ref = _setup(tmp_path)
    out = _run(ws, ref, "zzz-not-in-body-zzzz")
    assert out.kept_count == 1
    assert out.problems[0].admission == Admission.QUARANTINED
    assert out.problems[0].evidence == []
    stored = ProblemRepository(ws, shadow=True).get(out.problems[0].problem_id)
    assert stored is not None and stored.admission == Admission.QUARANTINED


def test_validate_upsert_verifies_good_quote(tmp_path):
    ws, ref = _setup(tmp_path)
    out = _run(ws, ref, "session restore failure")
    assert out.problems[0].admission == Admission.VERIFIED
    assert len(out.problems[0].evidence) == 1


def test_validate_upsert_protects_accepted_unless_force(tmp_path):
    ws, ref = _setup(tmp_path)
    first = _run(ws, ref, "session restore failure")
    pid = first.problems[0].problem_id
    repo = ProblemRepository(ws, shadow=True)
    accepted = repo.get(pid)
    accepted.admission = Admission.ACCEPTED
    accepted.admission_reason = "human_accepted"
    repo.upsert(accepted)

    # Re-upsert with an invented quote -> would quarantine; must stay accepted.
    out = _run(ws, ref, "zzz-not-in-body-zzzz")
    assert out.problems[0].admission == Admission.ACCEPTED
    assert repo.get(pid).admission == Admission.ACCEPTED
    assert repo.get(pid).admission_reason == "human_accepted"

    forced = _run(ws, ref, "zzz-not-in-body-zzzz", force=True)
    assert forced.problems[0].admission == Admission.QUARANTINED
    assert repo.get(pid).admission == Admission.QUARANTINED


@pytest.mark.parametrize("force", [False, True])
def test_validate_upsert_new_problem_unaffected_by_force(tmp_path, force):
    ws, ref = _setup(tmp_path)
    out = _run(ws, ref, "session restore failure", force=force)
    assert out.problems[0].admission == Admission.VERIFIED


def test_discover_problems_keeps_accepted_on_force_reextract(tmp_path):
    """force recomputes extraction; it must not demote a human-accepted card."""
    ws = Workspace(tmp_path)
    ws.ensure()
    ref = "github:issue:1"
    body = "crashes on import when the session is large"
    ArtifactRepository(ws).upsert(_art(ref, body))

    class _VerifiedRunner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            return ProblemsExtraction(
                problems=[
                    ProblemDraft(
                        title="Session crashes",
                        user_type="developer",
                        evidence=[
                            ProblemEvidenceDraft(source_ref=ref, quote="crashes on import")
                        ],
                    )
                ]
            )

    kwargs = _discover_kwargs(ws)
    first = discover_problems(_VerifiedRunner(), **kwargs)
    assert first is not None and len(first) == 1
    assert first[0].admission == Admission.VERIFIED
    pid = first[0].problem_id

    repo = ProblemRepository(ws)
    accepted = repo.get(pid)
    assert accepted is not None
    accepted.admission = Admission.ACCEPTED
    accepted.admission_reason = "human_accepted"
    repo.upsert(accepted)

    class _RefreshRunner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            return ProblemsExtraction(
                problems=[
                    ProblemDraft(
                        title="Session crashes",
                        user_type="operator",
                        evidence=[
                            ProblemEvidenceDraft(source_ref=ref, quote="crashes on import")
                        ],
                    ),
                    ProblemDraft(
                        title="New timeout",
                        evidence=[
                            ProblemEvidenceDraft(source_ref=ref, quote="session is large")
                        ],
                    ),
                ]
            )

    refreshed = discover_problems(_RefreshRunner(), force=True, **kwargs)
    assert refreshed is not None
    by_title = {p.title: p for p in refreshed}

    kept = by_title["Session crashes"]
    assert kept.problem_id == pid
    assert kept.user_type == "operator"
    assert kept.admission == Admission.ACCEPTED
    assert kept.admission_reason == "human_accepted"
    stored = repo.get(pid)
    assert stored is not None
    assert stored.admission == Admission.ACCEPTED
    assert stored.admission_reason == "human_accepted"
    assert stored.user_type == "operator"

    fresh = by_title["New timeout"]
    assert fresh.problem_id != pid
    assert fresh.admission == Admission.VERIFIED
    assert fresh.admission_reason == "has_valid_evidence"
    stored_fresh = repo.get(fresh.problem_id)
    assert stored_fresh is not None
    assert stored_fresh.admission == Admission.VERIFIED
