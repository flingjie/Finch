"""Tests for PracticeProfile models + YAML load/save (用户真实实践画像)."""

import yaml

from finch.profile.models import (
    PracticeEvidenceStatus,
    PracticeItem,
    PracticeProfile,
    load_practice_profile,
    save_practice_profile,
)


def _item(**kw) -> PracticeItem:
    data = dict(
        id="agent-100-days",
        domain="agent engineering",
        claim="把 Agent 落地失败整理成 100 天路径",
        evidence_refs=["https://github.com/flingjie/Agent-100-Days"],
        status=PracticeEvidenceStatus.SOURCED,
        can_offer=["方法卡", "案例"],
        boundaries="没管过生产 Agent SLA",
        confirmed=True,
    )
    data.update(kw)
    return PracticeItem(**data)


def test_load_missing_file_returns_empty(tmp_path):
    p = load_practice_profile(tmp_path / "nope.yaml")
    assert p.is_empty()
    assert p.items == []


def test_load_empty_file_returns_empty(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("")
    assert load_practice_profile(f).is_empty()


def test_load_corrupt_yaml_returns_empty(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text("items: [unclosed")
    p = load_practice_profile(f)
    assert p.is_empty()
    assert "practice-profile" in capsys.readouterr().err


def test_load_non_list_items_returns_empty(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text("items: true\n")
    p = load_practice_profile(f)
    assert p.is_empty()
    assert p.items == []
    assert "practice-profile" in capsys.readouterr().err


def test_save_and_load_roundtrip(tmp_path):
    f = tmp_path / "p.yaml"
    save_practice_profile(PracticeProfile(items=[_item()]), f)
    back = load_practice_profile(f)
    assert len(back.items) == 1
    assert back.items[0].id == "agent-100-days"
    assert back.items[0].status == PracticeEvidenceStatus.SOURCED
    assert yaml.safe_load(f.read_text())["items"][0]["confirmed"] is True


def test_confirmed_items_filters_unconfirmed():
    p = PracticeProfile(items=[_item(), _item(id="x", confirmed=False)])
    assert [i.id for i in p.confirmed_items()] == ["agent-100-days"]
    assert not p.is_empty()


def test_is_empty_when_no_confirmed_items():
    p = PracticeProfile(items=[_item(confirmed=False)])
    assert p.is_empty()


def test_sourced_without_refs_is_rejected_at_load(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "items": [
                    {"id": "bad", "domain": "d", "claim": "c", "evidence_refs": [],
                     "status": "sourced", "confirmed": True},
                    {"id": "ok", "domain": "d", "claim": "c", "evidence_refs": [],
                     "status": "author_stated", "confirmed": True},
                ]
            },
            allow_unicode=True,
        )
    )
    p = load_practice_profile(f)
    assert [i.id for i in p.items] == ["ok"]
    assert "bad" in capsys.readouterr().err


def test_duplicate_ids_keep_first_and_warn(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text(
        yaml.safe_dump(
            {
                "items": [
                    {"id": "dup", "domain": "a", "claim": "first", "evidence_refs": [],
                     "status": "author_stated"},
                    {"id": "dup", "domain": "b", "claim": "second", "evidence_refs": [],
                     "status": "author_stated"},
                ]
            },
            allow_unicode=True,
        )
    )
    p = load_practice_profile(f)
    assert len(p.items) == 1
    assert p.items[0].claim == "first"
    assert "dup" in capsys.readouterr().err


def test_get_by_id():
    p = PracticeProfile(items=[_item()])
    assert p.get("agent-100-days") is not None
    assert p.get("missing") is None
