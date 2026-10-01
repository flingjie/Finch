"""从用户自己的仓库 README 起草未确认的实践条目（finch profile init）。

LLM 只产候选；``status`` / ``evidence_refs`` / ``confirmed=False`` 由代码确定性设置。
README 作为数据传入 prompt，并明示不得当作指令。
"""

import re
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.profile.models import PracticeEvidenceStatus, PracticeItem, PracticeProfile

_PROMPT = Path("prompts/practice-profile-draft.md")
_README_LIMIT = 6000
_SLUG = re.compile(r"[^a-z0-9-]+")


class PracticeItemDraft(BaseModel):
    id: str = ""
    domain: str = ""
    claim: str = ""
    can_offer: list[str] = Field(default_factory=list)
    boundaries: str = ""


class PracticeDraftOutput(BaseModel):
    items: list[PracticeItemDraft] = Field(default_factory=list)


def _slug(raw: str) -> str:
    return _SLUG.sub("-", raw.strip().lower()).strip("-")


def draft_items_from_readme(
    runner: StructuredInferenceRunner, *, repo: str, readme: str
) -> list[PracticeItem]:
    """README → 未确认 PracticeItem 列表；LLM 失败或无内容 → []（fail-soft）。"""
    text = (readme or "").strip()
    if not text:
        return []
    prompt = _PROMPT.read_text().format(repo=repo, readme=text[:_README_LIMIT])
    try:
        out = cast(PracticeDraftOutput, runner.run(prompt, PracticeDraftOutput))
    except Exception:
        return []
    items: list[PracticeItem] = []
    for d in out.items:
        slug = _slug(d.id)
        if not slug or not d.claim.strip():
            continue
        items.append(
            PracticeItem(
                id=slug,
                domain=d.domain.strip() or "unknown",
                claim=d.claim.strip(),
                evidence_refs=[f"https://github.com/{repo}"],
                status=PracticeEvidenceStatus.SOURCED,
                can_offer=[c.strip() for c in d.can_offer if c.strip()],
                boundaries=d.boundaries.strip(),
                confirmed=False,
            )
        )
    return items


def merge_drafts(
    profile: PracticeProfile, drafts: list[PracticeItem]
) -> tuple[PracticeProfile, list[str]]:
    """只追加新 id，绝不覆盖已有条目（幂等）；返回 (新画像, 新增 id 列表)。"""
    existing = {i.id for i in profile.items}
    added: list[str] = []
    items = list(profile.items)
    for d in drafts:
        if d.id in existing:
            continue
        items.append(d)
        existing.add(d.id)
        added.append(d.id)
    return PracticeProfile(items=items), added
