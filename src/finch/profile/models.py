"""PracticeProfile：用户亲自确认的真实实践清单（唯一真相源，纯本地 YAML）。

与 ``content/voice.py`` 同范式：文件缺失 / 空 / 损坏 → 空画像；只有 ``confirmed: true``
的条目会被渲染进任何 prompt。``evidence_refs`` 为空的条目必须是 ``author_stated``。
"""

import sys
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


class PracticeEvidenceStatus(StrEnum):
    SOURCED = "sourced"  # 有公开 URL 可引用
    AUTHOR_STATED = "author_stated"  # 用户亲口陈述，无公开证据


class PracticeItem(BaseModel):
    """一条「我真的做过的事」。"""

    id: str
    domain: str
    claim: str
    evidence_refs: list[str] = Field(default_factory=list)
    status: PracticeEvidenceStatus
    can_offer: list[str] = Field(default_factory=list)
    boundaries: str = ""
    confirmed: bool = False

    @model_validator(mode="after")
    def _refs_match_status(self) -> "PracticeItem":
        if not self.evidence_refs and self.status == PracticeEvidenceStatus.SOURCED:
            raise ValueError(
                f"practice item {self.id!r}: status 'sourced' requires evidence_refs"
            )
        return self


class PracticeProfile(BaseModel):
    items: list[PracticeItem] = Field(default_factory=list)

    def confirmed_items(self) -> list[PracticeItem]:
        return [i for i in self.items if i.confirmed]

    def is_empty(self) -> bool:
        """无已确认条目即为空（未确认草稿不算）。"""
        return not self.confirmed_items()

    def get(self, item_id: str) -> PracticeItem | None:
        for item in self.items:
            if item.id == item_id:
                return item
        return None


def _warn(msg: str) -> None:
    print(f"[practice-profile] {msg}", file=sys.stderr)


def load_practice_profile(path: Path | str) -> PracticeProfile:
    """加载画像；缺失 / 空 / 损坏 → 空画像；单条校验失败只丢弃该条并告警。"""
    target = Path(path)
    if not target.exists():
        return PracticeProfile()
    try:
        data = yaml.safe_load(target.read_text()) or {}
    except yaml.YAMLError as exc:
        _warn(f"{target}: invalid YAML, using empty profile ({exc})")
        return PracticeProfile()
    if not isinstance(data, dict):
        return PracticeProfile()
    raw_items = data.get("items")
    if raw_items is None:
        raw_items = []
    elif not isinstance(raw_items, list):
        _warn(f"{target}: items must be a list, using empty profile (got {type(raw_items).__name__})")
        raw_items = []
    items: list[PracticeItem] = []
    seen: set[str] = set()
    for raw in raw_items:
        try:
            item = PracticeItem.model_validate(raw)
        except ValidationError as exc:
            ident = raw.get("id", "?") if isinstance(raw, dict) else "?"
            _warn(f"skip item {ident!r}: {exc.errors()[0].get('msg', exc)}")
            continue
        if item.id in seen:
            _warn(f"skip duplicate item id {item.id!r} (keeping first)")
            continue
        seen.add(item.id)
        items.append(item)
    return PracticeProfile(items=items)


def save_practice_profile(profile: PracticeProfile, path: Path | str) -> None:
    """写回 YAML（临时文件 + os.replace 原子覆盖，幂等）。"""
    import os
    import uuid

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(
        yaml.safe_dump(profile.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )
    os.replace(tmp, target)
