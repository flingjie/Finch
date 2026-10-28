"""Solution Explorer 单测：确定性门禁、证据子集、幂等。"""

from __future__ import annotations

from pydantic import BaseModel

from finch.products.explorer import SolutionDraft, build_solution, explore_product
from finch.products.ids import solution_id_for
from finch.products.models import (
    EvidenceStatus,
    Product,
    SolutionEvidence,
    SolutionVerdict,
)
from finch.products.repository import SolutionRepository
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
