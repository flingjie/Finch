"""Solution Explorer 单测：确定性门禁、证据子集、幂等。"""

from __future__ import annotations

from pydantic import BaseModel

from finch.products.explorer import (
    FeedbackOpportunityDraft,
    SolutionDraft,
    build_solution,
    explore_product,
)
from finch.products.ids import solution_id_for
from finch.products.models import (
    EvidenceStatus,
    Feedback,
    FeedbackReport,
    Product,
    SolutionEvidence,
    SolutionVerdict,
)
from finch.products.repository import SolutionRepository
from finch.sources.models import Source
from finch.storage.workspace import Workspace


def _product() -> Product:
    return Product(
        id="prod_x",
        key="github.com/acme/widget",
        source_refs=["twitter:post:1"],
        fingerprint="fp123",
    )


def _valid_draft() -> SolutionDraft:
    return SolutionDraft(
        who_problem="QA engineers with flaky tests",
        prior_approach="manual reruns",
        new_solution="agent harness with deterministic gates",
        why_more_valuable="fewer false retries",
        value_evidence=[
            SolutionEvidence(
                claim="cuts retries", source_refs=["twitter:post:1"], status=EvidenceStatus.EXPLICIT
            )
        ],
        design_thinking="gate on uncertainty",
        builders="Alice",
        verdict="worth_trial",
    )


def test_build_solution_requires_core_fields():
    assert build_solution(_valid_draft(), _product()) is not None
    draft = _valid_draft().model_copy(update={"who_problem": ""})
    assert build_solution(draft, _product()) is None


def test_build_solution_drops_unevidenced_refs_and_coerces_verdict():
    draft = _valid_draft().model_copy(
        update={
            "value_evidence": [
                SolutionEvidence(
                    claim="no ref",
                    source_refs=["other:ref"],
                    status=EvidenceStatus.EXPLICIT,
                )
            ],
            "verdict": "not-a-real-verdict",
        }
    )
    solution = build_solution(draft, _product())
    assert solution is not None
    assert solution.value_evidence[0].source_refs == []
    assert solution.value_evidence[0].status == EvidenceStatus.ASSUMPTION
    assert solution.verdict == SolutionVerdict.WATCH


class _BoomRunner:
    def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
        raise AssertionError("should not be called when idempotent")


def test_explore_product_is_idempotent(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = SolutionRepository(ws)
    product = _product()
    expected_id = solution_id_for(product.id, product.fingerprint, version="product-solution-v1")
    from finch.products.models import Solution

    repo.upsert(Solution(id=expected_id, product_id=product.id))
    result = explore_product(_BoomRunner(), product, [], repo)
    assert result is not None
    assert result.id == expected_id


def test_explore_product_force_reruns(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = SolutionRepository(ws)
    product = _product()

    class _Runner:
        def run(self, prompt: str, output_model: type[BaseModel], *, timeout: float = 600.0):
            assert output_model is SolutionDraft
            return _valid_draft()

    result = explore_product(_Runner(), product, [], repo, force=True)
    assert result is not None
    assert result.who_problem == "QA engineers with flaky tests"


def _feedback_report() -> FeedbackReport:
    return FeedbackReport(
        report_id="fbr_x",
        product_id="prod_x",
        feedback=[
            Feedback(
                feedback_id="fb_1",
                product_id="prod_x",
                source=Source.REDDIT,
                source_url="https://reddit.com/r/x/1",
                original_excerpt="crashes on import",
            )
        ],
    )


def test_build_solution_accepts_feedback_refs_and_drops_others():
    report = _feedback_report()
    draft = _valid_draft().model_copy(
        update={
            "value_evidence": [
                SolutionEvidence(
                    claim="users complain",
                    feedback_refs=["fb_1"],
                    status=EvidenceStatus.EXPLICIT,
                ),
                SolutionEvidence(
                    claim="no refs",
                    feedback_refs=["fb_nope"],
                    status=EvidenceStatus.EXPLICIT,
                ),
            ]
        }
    )
    solution = build_solution(draft, _product(), feedback_report=report)
    assert solution is not None
    assert solution.value_evidence[0].origin == "feedback"
    assert solution.value_evidence[0].feedback_refs == ["fb_1"]
    assert solution.value_evidence[0].status == EvidenceStatus.EXPLICIT
    assert solution.value_evidence[1].origin == "inference"
    assert solution.value_evidence[1].feedback_refs == []
    assert solution.value_evidence[1].status == EvidenceStatus.ASSUMPTION


def test_build_solution_coerces_feedback_opportunities():
    report = _feedback_report()
    draft = _valid_draft().model_copy(
        update={
            "feedback_opportunities": [
                FeedbackOpportunityDraft(
                    kind="pain_point", claim="import crash", feedback_refs=["fb_1"], status="explicit"
                ),
                FeedbackOpportunityDraft(kind="bogus", claim="drop me", feedback_refs=["fb_1"]),
            ]
        }
    )
    solution = build_solution(draft, _product(), feedback_report=report)
    assert solution is not None
    assert [o.kind.value for o in solution.feedback_opportunities] == ["pain_point"]


def test_solution_invalidates_on_feedback_change():
    draft = _valid_draft()
    p = _product()
    s1 = build_solution(draft, p, feedback_report=_feedback_report())
    report2 = _feedback_report().model_copy(update={"report_id": "fbr_y"})
    s2 = build_solution(draft, p, feedback_report=report2)
    assert s1 is not None and s2 is not None
    assert s1.id != s2.id
    assert s1.feedback_report_id == "fbr_x"
