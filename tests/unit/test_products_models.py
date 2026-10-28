"""产品领域模型与 ID 的确定性单测。"""

from __future__ import annotations

from finch.products.ids import founder_id_for, product_id_for, solution_id_for
from finch.products.models import (
    EvidenceStatus,
    Product,
    ProductStatus,
    Solution,
    SolutionVerdict,
)


def test_product_id_is_deterministic():
    assert product_id_for("github.com/acme/widget") == product_id_for(
        "GITHUB.COM/acme/widget"
    )
    assert product_id_for("github.com/acme/widget").startswith("prod_")


def test_founder_and_solution_ids_are_deterministic():
    assert founder_id_for("github", "acme") == founder_id_for("github", "ACME")
    assert solution_id_for("prod_abc", "fp", version="v1") == solution_id_for(
        "prod_abc", "fp", version="v1"
    )
    assert solution_id_for("prod_abc", "fp", version="v1") != solution_id_for(
        "prod_abc", "fp", version="v2"
    )


def test_product_defaults_and_enum():
    p = Product(id="prod_x", key="github.com/acme/widget")
    assert p.status == ProductStatus.CANDIDATE
    assert p.score_total == 0.0
    assert p.card_version == ""


def test_solution_verdict_and_evidence_defaults():
    s = Solution(id="sol_x", product_id="prod_x")
    assert s.verdict == SolutionVerdict.WATCH
    assert s.value_evidence == []
    assert s.version == "product-solution-v1"


def test_evidence_status_values():
    assert EvidenceStatus.EXPLICIT.value == "explicit"
    assert EvidenceStatus.INFERRED.value == "inferred"
    assert EvidenceStatus.ASSUMPTION.value == "assumption"
