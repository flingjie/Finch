"""问题发现领域模型 + ID 单测：确定性、默认值、枚举。"""

from __future__ import annotations

from finch.problem_discovery.ids import discussion_id_for, problem_id_for
from finch.problem_discovery.models import (
    EvidenceLevel,
    Problem,
    ProblemEvidence,
    ProblemStatus,
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
