"""问题发现：LLM 抽取问题卡片 + 确定性门禁 + 幂等缓存。

镜像 ``products/feedback.py`` 的「LLM 草稿 → 确定性 build_* 门控」：``evidence`` 的
``source_ref`` 必须在已导入来源中，非空 ``quote`` 规范化后必须是来源文本的子串（防伪造）；
无有效证据时在 ``unknowns`` 追加「未验证」标记。事实（evidence）与假设（hypotheses）/
未知（unknowns）在结构上分开。LLM 不写任何总分。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.problem_discovery.ids import problem_id_for
from finch.problem_discovery.models import (
    DiscoveryRun,
    EvidenceLevel,
    ExperienceLevel,
    Problem,
    ProblemEvidence,
    ProblemScenario,
    ProblemStatus,
)
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.sources.store import ArtifactRepository

_PROMPT = Path("prompts/problem-discovery.md")
PROBLEM_VERSION = "problem-v1"


class ProblemEvidenceDraft(BaseModel):
    """LLM 输出的单条证据（enum 用 str 宽容解析，代码再强制纠正）。"""

    source_ref: str = ""
    url: str = ""
    quote: str = ""
    claim: str = ""
    evidence_level: str = "e1"
    experience: str = "unknown"
    is_marketing: bool = False
    author: str = ""


class ProblemDraft(BaseModel):
    """LLM 输出的单张问题卡片（事实/假设/未知分列）。"""

    title: str = ""
    user_type: str = ""
    task: str = ""
    trigger: str = ""
    consequence: str = ""
    current_solutions: list[str] = Field(default_factory=list)
    evidence: list[ProblemEvidenceDraft] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    next_action: str = ""


class ProblemsExtraction(BaseModel):
    """一次 LLM 抽取调用返回：多张问题卡片。"""

    problems: list[ProblemDraft] = Field(default_factory=list)
    eval_failed: bool = False
    skip_reason: str = ""


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _fingerprint(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _coerce_level(value: str) -> EvidenceLevel:
    try:
        return EvidenceLevel(value)
    except ValueError:
        return EvidenceLevel.E1


def _coerce_experience(value: str) -> ExperienceLevel:
    try:
        return ExperienceLevel(value)
    except ValueError:
        return ExperienceLevel.UNKNOWN


def render_candidates_json(source_texts: dict[str, str], urls: dict[str, str]) -> str:
    rows = [
        {"source_ref": ref, "url": urls.get(ref, ""), "text": text}
        for ref, text in sorted(source_texts.items())
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def assess_problems(runner: StructuredInferenceRunner, candidates_json: str) -> ProblemsExtraction:
    """LLM 从原始材料抽取问题卡片；失败 → eval_failed（fail-soft）。"""
    prompt = _PROMPT.read_text().replace("{candidates_json}", candidates_json)
    try:
        return cast(ProblemsExtraction, runner.run(prompt, ProblemsExtraction))
    except Exception:
        return ProblemsExtraction(eval_failed=True, skip_reason="问题抽取失败（LLM 异常）")


def build_problem(
    draft: ProblemDraft,
    *,
    source_texts: dict[str, str],
    urls: dict[str, str],
    now: datetime,
) -> Problem | None:
    """确定性把 draft 转为 Problem；空标题丢弃；evidence 必须回溯到来源，否则丢弃。

    无有效 evidence 时在 unknowns 追加「未验证：无证据」，不把推测当事实。
    """
    if not draft.title.strip():
        return None

    normalized = {ref: _normalize(t) for ref, t in source_texts.items()}
    evidence: list[ProblemEvidence] = []
    for ev in draft.evidence:
        ref = ev.source_ref.strip()
        if ref not in source_texts:
            continue  # 来源不可回溯 → 丢弃
        quote_norm = _normalize(ev.quote)
        if quote_norm and quote_norm not in normalized[ref]:
            continue  # 摘录无法回溯到来源 → 丢弃（防伪造）
        evidence.append(
            ProblemEvidence(
                source_ref=ref,
                url=ev.url.strip() or urls.get(ref, ""),
                quote=ev.quote.strip(),
                claim=ev.claim.strip(),
                evidence_level=_coerce_level(ev.evidence_level),
                experience=_coerce_experience(ev.experience),
                is_marketing=ev.is_marketing,
                author=ev.author.strip(),
            )
        )

    unknowns = [u.strip() for u in draft.unknowns if u.strip()]
    if not evidence:
        marker = "未验证：无证据"
        if marker not in unknowns:
            unknowns.append(marker)

    title = draft.title.strip()
    return Problem(
        problem_id=problem_id_for(title),
        title=title,
        user_type=draft.user_type.strip(),
        scenario=ProblemScenario(
            task=draft.task.strip(),
            trigger=draft.trigger.strip(),
            consequence=draft.consequence.strip(),
        ),
        current_solutions=[s.strip() for s in draft.current_solutions if s.strip()],
        evidence=evidence,
        hypotheses=[h.strip() for h in draft.hypotheses if h.strip()],
        unknowns=unknowns,
        next_action=draft.next_action.strip(),
        source_refs=list(dict.fromkeys(e.source_ref for e in evidence)),
        status=ProblemStatus.NEW,
    )


def _collect_sources(
    artifact_repo: ArtifactRepository, discussion_repo: DiscussionRepository
) -> tuple[dict[str, str], dict[str, str]]:
    source_texts: dict[str, str] = {}
    urls: dict[str, str] = {}
    for art in artifact_repo.list_all():
        if art.text.strip():
            source_texts[art.artifact_id] = art.text
            urls[art.artifact_id] = art.canonical_url
    for disc in discussion_repo.list_all():
        if disc.text.strip():
            source_texts[disc.discussion_id] = disc.text
            urls[disc.discussion_id] = disc.url
    return source_texts, urls


def discover_problems(
    runner: StructuredInferenceRunner | None,
    *,
    problem_repo: ProblemRepository,
    artifact_repo: ArtifactRepository,
    discussion_repo: DiscussionRepository,
    run_repo: DiscoveryRunRepository,
    version: str = PROBLEM_VERSION,
    force: bool = False,
    now: datetime | None = None,
) -> list[Problem] | None:
    """从已导入来源抽取问题卡片并入库；幂等（同来源指纹复用缓存），``force`` 重算。

    无来源或无 runner → None。
    """
    source_texts, urls = _collect_sources(artifact_repo, discussion_repo)
    if not source_texts:
        return None

    fingerprint = _fingerprint({"sources": sorted(source_texts), "version": version})
    clock = now or datetime.now(UTC)
    if not force:
        latest = run_repo.latest()
        if latest is not None and latest.fingerprint == fingerprint:
            return problem_repo.list_all()
    if runner is None:
        return None

    draft = assess_problems(runner, render_candidates_json(source_texts, urls))
    problems: list[Problem] = []
    for pd in draft.problems:
        problem = build_problem(pd, source_texts=source_texts, urls=urls, now=clock)
        if problem is not None:
            problem_repo.upsert(problem)
            problems.append(problem)

    run_repo.upsert(
        DiscoveryRun(
            run_id=f"run_{clock:%Y%m%d%H%M%S}",
            created_at=clock,
            fingerprint=fingerprint,
            problem_ids=[p.problem_id for p in problems],
        )
    )
    return problems
