"""Draft 语义审查（contract C5）：六维打分 + 三个语义 flag + 蕴含判定。

同时承载 Critic Suite 默认检查器套件（``default_checker_suite``）与并行执行器
（``_run_checks``），供 graph 的 write 节点与 idea 服务复用。
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.content.checkers.aggregate import aggregate_checks
from finch.content.checkers.base import CheckContext, Checker, CheckResult
from finch.content.checkers.decision import DecisionChecker
from finch.content.checkers.evidence import EvidenceChecker
from finch.content.checkers.portability import PortabilityChecker
from finch.content.checkers.responsiveness import ResponsivenessChecker
from finch.content.checkers.safety import SafetyChecker
from finch.content.checkers.specificity import SpecificityChecker
from finch.content.checkers.structure import StructureChecker
from finch.content.checkers.voice import VoiceChecker
from finch.content.models import Draft
from finch.content.voice import VoiceProfile
from finch.evidence.models import EvidenceCard
from finch.llm.base import StructuredInferenceRunner
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


def default_checker_suite(
    runner: StructuredInferenceRunner | None,
    voice_profile: VoiceProfile | None = None,
) -> list[Checker]:
    """Critic Suite 默认检查器套件：7 个检查器。

    顺序即执行顺序；VoiceChecker 需要 VoiceProfile（默认空画像）。
    """
    profile = voice_profile if voice_profile is not None else VoiceProfile()
    return [
        EvidenceChecker(runner),
        DecisionChecker(runner),
        SpecificityChecker(runner),
        PortabilityChecker(runner),
        VoiceChecker(runner, profile),
        StructureChecker(runner),
        SafetyChecker(runner),
        ResponsivenessChecker(),
    ]


def _run_checks(suite: list[Checker], check_ctx: CheckContext) -> list[CheckResult]:
    """并行执行 Critic Suite，结果顺序与串行一致（``pool.map`` 保序）。

    单个 checker 退化为串行；多个 checker 时以 ``len(suite)`` 个 worker 并行，
    各自内部状态只读（CodexRunner 每次调用独立子进程 + 临时目录），线程安全。
    """
    if len(suite) <= 1:
        return [checker.check(check_ctx) for checker in suite]
    with ThreadPoolExecutor(max_workers=len(suite)) as pool:
        return list(pool.map(lambda checker: checker.check(check_ctx), suite))
