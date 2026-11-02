"""问题发现领域模型 + ID 单测：确定性、默认值、枚举。"""

from __future__ import annotations

from finch.problem_discovery.ids import discussion_id_for, problem_id_for
from finch.problem_discovery.models import (
    Admission,
    EvidenceLevel,
    Problem,
    ProblemEvidence,
    ProblemStatus,
    has_unresolved_conflict,
)


def test_problem_id_deterministic_and_prefixed():
    assert problem_id_for("Agent 失败后难以复现") == problem_id_for("Agent 失败后难以复现")
    assert problem_id_for("Agent 失败后难以复现").startswith("prob_")
    assert problem_id_for("a") != problem_id_for("b")


def test_problem_id_normalizes_whitespace_and_case():
    assert problem_id_for("Agent fails") == problem_id_for("  agent   FAILS ")


def test_discussion_id_deterministic():
    assert discussion_id_for("https://x.com/a/1", "body").startswith("disc_")
    assert discussion_id_for("u", "t") == discussion_id_for("u", "t")


def test_problem_defaults():
    p = Problem()
    assert p.problem_id == ""
    assert p.title == ""
    assert p.user_type == ""
    assert p.scenario.task == "" and p.scenario.trigger == "" and p.scenario.consequence == ""
    assert p.current_solutions == []
    assert p.evidence == []
    assert p.hypotheses == []
    assert p.unknowns == []
    assert p.next_action == ""
    assert p.source_refs == []
    assert p.status == ProblemStatus.NEW


def test_problem_evidence_defaults():
    e = ProblemEvidence()
    assert e.source_ref == ""
    assert e.evidence_level == EvidenceLevel.E1
    assert e.experience.value == "unknown"
    assert e.is_marketing is False


def test_enum_values():
    assert EvidenceLevel.E0.value == "e0"
    assert EvidenceLevel.E4.value == "e4"
    assert ProblemStatus.NEW.value == "new"


def test_admission_source_checked_value():
    assert Admission.SOURCE_CHECKED.value == "source_checked"
    assert not hasattr(Admission, "VERIFIED")


def test_legacy_verified_admission_maps_to_source_checked():
    # 旧磁盘数据 admission: verified → 读取映射，不报错（验收案例 6）
    p = Problem.model_validate(
        {"problem_id": "prob_old", "title": "t", "admission": "verified"}
    )
    assert p.admission == Admission.SOURCE_CHECKED


def test_legacy_dict_without_admission_infers_source_checked():
    raw = {
        "problem_id": "prob_legacy",
        "title": "legacy",
        "evidence": [{"source_ref": "s1", "quote": "q", "claim": "c"}],
    }
    assert Problem.model_validate(raw).admission == Admission.SOURCE_CHECKED


def test_claim_support_defaults_unknown():
    e = ProblemEvidence(source_ref="s", quote="q")
    assert e.claim_support == "unknown"
    assert e.claim_support_reason == ""


def test_has_unresolved_conflict():
    clean = Problem(
        problem_id="p1",
        title="t",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    conflicted = Problem(
        problem_id="p2",
        title="t",
        evidence=[
            ProblemEvidence(source_ref="s", quote="q"),
            ProblemEvidence(
                source_ref="s2",
                quote="q2",
                claim_support="conflicting",
                claim_support_reason="摘录与主张相反",
            ),
        ],
    )
    assert not has_unresolved_conflict(clean)
    assert has_unresolved_conflict(conflicted)
