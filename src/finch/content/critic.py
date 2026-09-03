"""Draft 语义审查（contract C5）：六维打分 + 三个语义 flag + 蕴含判定。"""

import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.content.checkers.aggregate import aggregate_checks
from finch.content.checkers.base import CheckResult
from finch.content.models import Draft
from finch.evidence.models import EvidenceCard
from finch.settings import QualityGates

_PROMPT_PATH = Path("prompts/critique-draft.md")


class CritiqueResult(BaseModel):
    passed: bool
    positioning: float = 0.0
    evidence: float = 0.0
    increment: float = 0.0
    conversation: float = 0.0
    voice: float = 0.0
    safety: float = 0.0
    quality_score: float = 0.0
    invented_personal_experience: bool = False
    unsupported_metric: bool = False
    entailment_failed: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)

    @classmethod
    def from_checks(cls, checks: list[CheckResult]) -> "CritiqueResult":
        """构建兼容性汇总：``passed`` 由确定性聚合器计算，绝不信任模型输出。"""
        return cls(passed=aggregate_checks(checks) == "pass", checks=checks)


def _render_draft(draft: Draft) -> str:
    payload = {
        "body": draft.body,
        "claims": [claim.model_dump(mode="json") for claim in draft.claims],
    }
    return json.dumps(payload, ensure_ascii=False)


def _render_cards(draft: Draft, cards_by_id: dict[str, EvidenceCard]) -> str:
    wanted = {claim.evidence_card_id for claim in draft.claims}
    cards = [cards_by_id[cid] for cid in sorted(wanted) if cid in cards_by_id]
    return json.dumps([card.model_dump(mode="json") for card in cards], ensure_ascii=False)


def critique(
    runner: CodexRunner,
    draft: Draft,
    cards_by_id: dict[str, EvidenceCard],
) -> CritiqueResult:
    prompt = _PROMPT_PATH.read_text().format(
        draft=_render_draft(draft),
        cards=_render_cards(draft, cards_by_id),
    )
    return cast(CritiqueResult, runner.run(prompt, CritiqueResult))


def evaluate_passed(result: CritiqueResult, gates: QualityGates) -> bool:
    return (
        result.quality_score >= gates.min_quality_score
        and not result.invented_personal_experience
        and not result.unsupported_metric
        and not result.entailment_failed
    )
