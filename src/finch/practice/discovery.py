"""PracticeCard + PracticeDiscoveryService：围绕一个问题发现可尝试的实践卡（≤3）。

``discover`` 分两步：
1. 已有记录（ExpressionMethod / KnowledgeCard / PracticeAttempt）→ 确定性转成卡，无需 LLM。
2. 可选外部来源（``--external``）→ 复用 ``DiscoveryOrchestrator`` 有界采集 + codex 语义判断。

LLM 只产出语义候选，Python 负责上限（≤3）、证据引用校验与幂等 id。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from finch.expression_methods.models import ExpressionMethod
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.knowledge.models import KnowledgeCard
from finch.llm.base import StructuredInferenceRunner
from finch.practice.attempts import PracticeAttempt
from finch.problems.models import ActiveProblem
from finch.sources.connectors import DiscoveryContext
from finch.sources.models import Source
from finch.sources.orchestrator import DiscoveryOrchestrator
from finch.storage.repositories import (
    KnowledgeCardRepository,
    PracticeAttemptRepository,
    ProblemRepository,
)

_EXTERNAL_PROMPT = Path("prompts/practice-discovery.md")

_SEARCH_SOURCES: tuple[Source, ...] = (
    Source.TWITTER,
    Source.REDDIT,
    Source.V2EX,
    Source.XIAOHONGSHU,
)
_EXTERNAL_QUERY_LIMIT = 5
_EXTERNAL_CANDIDATE_CAP = 10


class PracticeCard(BaseModel):
    """一张发现卡：为什么相关、证据是什么、适用条件、最小行动。"""

    id: str
    problem_id: str
    claim: str  # 方法/结论一句话
    why_relevant: str = ""  # 为什么对当前问题相关
    evidence_refs: list[str] = Field(default_factory=list)
    excerpt: str = ""
    applicability: str = ""  # 适用条件/边界
    smallest_action: str  # 最小可执行动作
    origin: Literal["existing", "external"]


class PracticeCardDraft(BaseModel):
    """LLM 发现卡输出（external 路径标 origin="external"）。"""

    claim: str
    why_relevant: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    excerpt: str = ""
    applicability: str = ""
    smallest_action: str


class PracticeDiscoveryOutput(BaseModel):
    cards: list[PracticeCardDraft] = Field(default_factory=list, max_length=3)


def card_id_for(problem_id: str, claim: str) -> str:
    """内容寻址 id：相同 (problem + claim) → 相同 id（幂等）。"""
    raw = "\n".join([problem_id, claim])
    return f"card_{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:8]}"


def _keys(text: str) -> set[str]:
    """语言无关的粗粒度 key：latin 词 + CJK 双字组。"""
    keys: set[str] = set()
    for m in re.findall(r"[a-z0-9]{2,}", text.casefold()):
        keys.add(m)
    cjk = "".join(ch for ch in text if "一" <= ch <= "鿿")
    for i in range(len(cjk) - 1):
        keys.add(cjk[i : i + 2])
    return keys


def problem_relevance(problem: ActiveProblem, text: str) -> float:
    """确定性相关性分（0–1）：问题 key 与候选文本的重叠度。

    独立于 ``match_interests``（peers/scoring.py），不改变其语义。
    """
    query = " ".join(
        [problem.title, problem.why_it_matters, problem.desired_outcome, problem.constraints]
    )
    qkeys = _keys(query)
    if not qkeys:
        return 0.0
    return len(qkeys & _keys(text)) / len(qkeys)


def _problem_query(problem: ActiveProblem) -> str:
    """由问题字段拼外部搜索 query（截断避免过长）。"""
    parts = [p for p in [problem.title, problem.desired_outcome] if p.strip()]
    return " ".join(parts)[:120]


def _dedupe_cards(cards: list[PracticeCard]) -> list[PracticeCard]:
    seen: set[str] = set()
    out: list[PracticeCard] = []
    for card in cards:
        if card.id in seen:
            continue
        seen.add(card.id)
        out.append(card)
    return out


class PracticeDiscoveryService:
    """围绕问题发现实践卡；确定性骨架 + 可选 LLM 外部判断。"""

    def __init__(
        self,
        *,
        problems: ProblemRepository,
        methods: ExpressionMethodRepository,
        knowledge: KnowledgeCardRepository,
        attempts: PracticeAttemptRepository,
        runner: StructuredInferenceRunner | None = None,
        orchestrator: DiscoveryOrchestrator | None = None,
    ) -> None:
        self.problems = problems
        self.methods = methods
        self.knowledge = knowledge
        self.attempts = attempts
        self.runner = runner
        self.orchestrator = orchestrator

    def discover(self, problem_id: str, *, external: bool = False) -> list[PracticeCard]:
        """返回 ≤3 张卡；已有记录在前、稳定 id 排序。"""
        problem = self._require_problem(problem_id)
        existing = self._existing_cards(problem)
        if external and self.orchestrator is not None and self.runner is not None:
            existing.extend(self._external_cards(problem, remaining=3 - len(existing)))
        return _dedupe_cards(existing)[:3]

    def _require_problem(self, problem_id: str) -> ActiveProblem:
        problem = self.problems.get(problem_id)
        if problem is None:
            raise KeyError(problem_id)
        return problem

    def _existing_cards(self, problem: ActiveProblem) -> list[PracticeCard]:
        scored: list[tuple[float, PracticeCard]] = []
        for m in self.methods.list_all():
            hit = self._card_from_method(problem, m)
            if hit is not None:
                scored.append(hit)
        for k in self.knowledge.list_all():
            hit = self._card_from_knowledge(problem, k)
            if hit is not None:
                scored.append(hit)
        for a in self.attempts.list_all():
            hit = self._card_from_attempt(problem, a)
            if hit is not None:
                scored.append(hit)
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [card for _, card in scored[:3]]

    def _card_from_method(
        self, problem: ActiveProblem, m: ExpressionMethod
    ) -> tuple[float, PracticeCard] | None:
        text = " ".join([m.title, m.why_effective, m.when_to_use, m.boundaries])
        score = problem_relevance(problem, text)
        if score <= 0:
            return None
        smallest = (m.mini_exercise or "").strip() or "按该方法做一次小练习并记录观察"
        card = PracticeCard(
            id=card_id_for(problem.id, m.title),
            problem_id=problem.id,
            claim=m.title,
            why_relevant=m.why_effective or m.when_to_use,
            evidence_refs=[m.id],
            excerpt=m.why_effective[:200],
            applicability=m.when_to_use,
            smallest_action=smallest,
            origin="existing",
        )
        return score, card

    def _card_from_knowledge(
        self, problem: ActiveProblem, k: KnowledgeCard
    ) -> tuple[float, PracticeCard] | None:
        text = " ".join([k.claim, k.applicability, k.limitations])
        score = problem_relevance(problem, text)
        if score <= 0:
            return None
        card = PracticeCard(
            id=card_id_for(problem.id, k.claim),
            problem_id=problem.id,
            claim=k.claim,
            why_relevant=k.applicability or k.claim,
            evidence_refs=[k.id],
            excerpt=k.claim[:200],
            applicability=k.applicability,
            smallest_action=f"在相关场景复用「{k.claim}」并记录结果",
            origin="existing",
        )
        return score, card

    def _card_from_attempt(
        self, problem: ActiveProblem, a: PracticeAttempt
    ) -> tuple[float, PracticeCard] | None:
        text = " ".join([a.attempt, a.observation, a.result])
        score = problem_relevance(problem, text)
        if score <= 0:
            return None
        claim = a.result or a.attempt
        card = PracticeCard(
            id=card_id_for(problem.id, claim),
            problem_id=problem.id,
            claim=claim,
            why_relevant=a.result or a.observation,
            evidence_refs=[a.id],
            excerpt=(a.observation or a.result)[:200],
            applicability="",
            smallest_action=a.next_step or "再次尝试并记录观察",
            origin="existing",
        )
        return score, card

    def _external_cards(self, problem: ActiveProblem, *, remaining: int) -> list[PracticeCard]:
        """有界外部采集 → codex 判断 → 卡；任一环节失败返回 []（不伪造）。"""
        if remaining <= 0:
            return []
        candidates = self._bounded_sync(problem)
        if not candidates:
            return []
        refs = {ref for _, ref in candidates}
        prompt = _EXTERNAL_PROMPT.read_text().format(
            title=problem.title,
            why_it_matters=problem.why_it_matters or "(none)",
            desired_outcome=problem.desired_outcome or "(none)",
            constraints=problem.constraints or "(none)",
            candidates="\n".join(f"- [{ref}] {text[:300]}" for text, ref in candidates),
        )
        try:
            output = cast(
                PracticeDiscoveryOutput,
                self.runner.run(prompt, PracticeDiscoveryOutput),  # type: ignore[union-attr]
            )
        except Exception:
            return []
        cards: list[PracticeCard] = []
        for d in output.cards[:remaining]:
            if not d.claim.strip() or not d.smallest_action.strip():
                continue
            valid_refs = [r for r in d.evidence_refs if r in refs]
            cards.append(
                PracticeCard(
                    id=card_id_for(problem.id, d.claim.strip()),
                    problem_id=problem.id,
                    claim=d.claim.strip(),
                    why_relevant=d.why_relevant,
                    evidence_refs=valid_refs,
                    excerpt=d.excerpt,
                    applicability=d.applicability,
                    smallest_action=d.smallest_action.strip(),
                    origin="external",
                )
            )
        return cards

    def _bounded_sync(self, problem: ActiveProblem) -> list[tuple[str, str]]:
        """返回 (text, ref) 列表；采集失败/无正文 → 更少或空。"""
        query = _problem_query(problem)
        if not query or self.orchestrator is None:
            return []
        out: list[tuple[str, str]] = []
        for src in _SEARCH_SOURCES:
            try:
                result = self.orchestrator.sync_source(
                    src, DiscoveryContext(queries=[query], limit=_EXTERNAL_QUERY_LIMIT)
                )
            except Exception:  # noqa: BLE001 — 单源失败不阻断其它源
                continue
            for art in result.artifacts:
                if (art.text or "").strip():
                    out.append((art.text, art.canonical_url or art.artifact_id))
            if len(out) >= _EXTERNAL_CANDIDATE_CAP:
                break
        return out[:_EXTERNAL_CANDIDATE_CAP]
