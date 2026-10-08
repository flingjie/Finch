"""AngleBrief / AngleCard / SourceSummary 契约校验。"""

import pytest
from pydantic import ValidationError

from finch.angle_discovery.models import AngleBrief, AngleCard, CombinationMaterial, SourceSummary


def _card(**overrides) -> AngleCard:
    data = dict(
        title="AI 写代码更快以后，交付为什么没有同步变快？",
        main_angles=["系统瓶颈", "真实场景映射"],
        target_reader="正在使用 AI 编码的小团队",
        thesis="当代码生成成本下降，规格澄清、审查和验收成为新的交付瓶颈。",
        combination_materials=[
            CombinationMaterial(role="原文观点", content="AI 让个人编码更快。", source="原文"),
            CombinationMaterial(
                role="第二份材料", content="交付由审查和验收收口。", source="领域通识"
            ),
        ],
        connection_rationale="第二份材料补上「个人效率为何没有转化为交付」的缺口。",
        incremental_value="从个人编码效率扩展到团队交付能力。",
        increment_basis="inference",
        opening_scene="一个假设团队一晚生成 20 个 PR，第二天只能认真审查 5 个。",
        evidence_gaps=["实际 PR 等待时间、审查耗时、返工原因"],
        reader_action="记录一周交付耗时，找出真正积压的环节。",
        writing_status="可先写机制分析",
    )
    data.update(overrides)
    return AngleCard(**data)


def _brief(**overrides) -> AngleBrief:
    data = dict(
        source_summary=SourceSummary(main_point="AI 让个人编码更快，但交付没有同步变快。"),
        angles=[_card()],
    )
    data.update(overrides)
    return AngleBrief(**data)


def test_rejects_empty_thesis():
    with pytest.raises(ValidationError):
        _card(thesis="")


def test_rejects_empty_title():
    with pytest.raises(ValidationError):
        _card(title="")


def test_rejects_unknown_increment_basis():
    with pytest.raises(ValidationError):
        _card(increment_basis="瞎写")


def test_rejects_extra_field():
    with pytest.raises(ValidationError):
        _card(rating=5)


def test_brief_allows_empty_angles():
    b = _brief(angles=[], recommended_index=None)
    assert b.angles == []
    assert b.recommended_index is None


def test_rejects_combination_material_missing_source():
    with pytest.raises(ValidationError):
        CombinationMaterial(role="原文观点", content="观点")


def test_rejects_combination_material_unknown_role():
    with pytest.raises(ValidationError):
        CombinationMaterial(role="随便", content="观点", source="原文")


def test_rejects_combination_material_extra_field():
    with pytest.raises(ValidationError):
        CombinationMaterial(role="原文观点", content="观点", source="原文", page=3)


def test_card_defaults_allow_no_combination():
    c = _card(combination_materials=[], connection_rationale="")
    assert c.combination_materials == []
    assert c.connection_rationale == ""


def test_rejects_empty_source_summary_main_point():
    with pytest.raises(ValidationError):
        SourceSummary(main_point="")


def test_minimal_brief_ok():
    b = _brief()
    assert b.id == ""
    assert b.coverage == []
    assert b.angles[0].increment_basis == "inference"
