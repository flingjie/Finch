"""问题发现：LLM 抽取问题卡片 + 确定性门禁 + 幂等缓存。

镜像 ``products/feedback.py`` 的「LLM 草稿 → 确定性 build_* 门控」：``evidence`` 的
``source_ref`` 必须在已导入来源中，非空 ``quote`` 规范化后必须是来源文本的子串（防伪造）；
URL 只取原始来源；证据等级由已核验材料确定性封顶。无有效证据时在 ``unknowns`` 追加
「未验证」标记。事实（evidence）与假设（hypotheses）/未知（unknowns）在结构上分开。
LLM 不写任何总分。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
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
PROBLEM_VERSION = "problem-v2"

_LEVEL_RANK = {
    EvidenceLevel.E0: 0,
    EvidenceLevel.E1: 1,
    EvidenceLevel.E2: 2,
    EvidenceLevel.E3: 3,
    EvidenceLevel.E4: 4,
}


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


def _content_sha(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()[:16]


def _fingerprint(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _coerce_level(value: str) -> EvidenceLevel:
    try:
        return EvidenceLevel(value)
    except ValueError:
        return EvidenceLevel.E0


def _coerce_experience(value: str) -> ExperienceLevel:
    try:
        return ExperienceLevel(value)
    except ValueError:
        return ExperienceLevel.UNKNOWN


def _cap_level(level: EvidenceLevel, cap: EvidenceLevel) -> EvidenceLevel:
    if _LEVEL_RANK[level] > _LEVEL_RANK[cap]:
        return cap
    return level


def _public_level_cap(
    *,
    is_marketing: bool,
    distinct_refs: int,
    distinct_authors: int,
) -> EvidenceLevel:
    """公开抓取材料的确定性等级上限：营销→E0；单源→E1；多独立→E2；永不 E3/E4。"""
    if is_marketing:
        return EvidenceLevel.E0
    if distinct_refs >= 2 or distinct_authors >= 2:
        return EvidenceLevel.E2
    return EvidenceLevel.E1


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

    URL 只取原始来源映射；空 quote 丢弃；证据等级按已核验材料封顶。
    无有效 evidence 时在 unknowns 追加「未验证：无证据」，不把推测当事实。
    """
    _ = now
    if not draft.title.strip():
        return None

    normalized = {ref: _normalize(t) for ref, t in source_texts.items()}
    pending: list[tuple[ProblemEvidenceDraft, str]] = []
    for ev in draft.evidence:
        ref = ev.source_ref.strip()
        if ref not in source_texts:
            continue  # 来源不可回溯 → 丢弃
        quote = ev.quote.strip()
        if not quote:
            continue  # 空摘录不可核验 → 丢弃
        quote_norm = _normalize(quote)
        if quote_norm not in normalized[ref]:
            continue  # 摘录无法回溯到来源 → 丢弃（防伪造）
        pending.append((ev, ref))

    distinct_refs = {ref for _, ref in pending}
    distinct_authors = {ev.author.strip() for ev, _ in pending if ev.author.strip()}

    evidence: list[ProblemEvidence] = []
    for ev, ref in pending:
        raw_level = _coerce_level(ev.evidence_level)
        cap = _public_level_cap(
            is_marketing=ev.is_marketing,
            distinct_refs=len(distinct_refs),
            distinct_authors=len(distinct_authors),
        )
        evidence.append(
            ProblemEvidence(
                source_ref=ref,
                url=urls.get(ref, ""),  # 忽略 LLM url，只用来源映射
                quote=ev.quote.strip(),
                claim=ev.claim.strip(),
                evidence_level=_cap_level(raw_level, cap),
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


def _source_timestamp(
    published_at: datetime | None, retrieved_at: datetime | None
) -> datetime | None:
    return published_at or retrieved_at


def _collect_sources(
    artifact_repo: ArtifactRepository,
    discussion_repo: DiscussionRepository,
    *,
    lookback_hours: int | None = None,
    max_sources: int | None = None,
    max_chars_per_source: int | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """收集可抽取来源；可选时间窗、条数上限与单源字符预算。"""
    clock = now or datetime.now(UTC)
    cutoff = (
        clock - timedelta(hours=lookback_hours) if lookback_hours and lookback_hours > 0 else None
    )
    candidates: list[tuple[datetime, str, str, str]] = []
    for art in artifact_repo.list_all():
        text = art.text.strip()
        if not text:
            continue
        ts = _source_timestamp(art.published_at, art.retrieved_at) or clock
        if cutoff is not None and ts < cutoff:
            continue
        candidates.append((ts, art.artifact_id, text, art.canonical_url))
    for disc in discussion_repo.list_all():
        text = disc.text.strip()
        if not text:
            continue
        ts = disc.retrieved_at or clock
        if cutoff is not None and ts < cutoff:
            continue
        candidates.append((ts, disc.discussion_id, text, disc.url))

    # 新近优先；同时间按 id 稳定排序。
    candidates.sort(key=lambda row: (-row[0].timestamp(), row[1]))
    if max_sources is not None and max_sources > 0:
        candidates = candidates[:max_sources]

    source_texts: dict[str, str] = {}
    urls: dict[str, str] = {}
    for _, ref, text, url in candidates:
        if max_chars_per_source is not None and max_chars_per_source > 0:
            text = text[:max_chars_per_source]
        source_texts[ref] = text
        urls[ref] = url
    return source_texts, urls


def _fingerprint_payload(source_texts: dict[str, str], version: str) -> dict[str, object]:
    sources = [
        {"id": ref, "content_sha256": _content_sha(text)}
        for ref, text in sorted(source_texts.items())
    ]
    return {"version": version, "sources": sources}


def _batches(items: list[str], batch_size: int) -> list[list[str]]:
    if batch_size <= 0 or batch_size >= len(items):
        return [items] if items else []
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]


def _replay_run_problems(
    problem_repo: ProblemRepository, problem_ids: list[str]
) -> list[Problem]:
    out: list[Problem] = []
    for pid in problem_ids:
        problem = problem_repo.get(pid)
        if problem is not None:
            out.append(problem)
    return out


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
    lookback_hours: int | None = 720,
    max_sources: int | None = 40,
    max_chars_per_source: int | None = 4000,
    batch_size: int | None = 10,
) -> list[Problem] | None:
    """从已导入来源抽取问题卡片并入库；幂等（同来源内容指纹复用缓存），``force`` 重算。

    无来源或无 runner → None。LLM ``eval_failed`` 不写运行缓存，避免把失败永久当空成功。
    """
    clock = now or datetime.now(UTC)
    source_texts, urls = _collect_sources(
        artifact_repo,
        discussion_repo,
        lookback_hours=lookback_hours,
        max_sources=max_sources,
        max_chars_per_source=max_chars_per_source,
        now=clock,
    )
    if not source_texts:
        return None

    fingerprint = _fingerprint(_fingerprint_payload(source_texts, version))
    if not force:
        latest = run_repo.latest()
        if latest is not None and latest.fingerprint == fingerprint:
            return _replay_run_problems(problem_repo, latest.problem_ids)
    if runner is None:
        return None

    refs = sorted(source_texts.keys())
    size = batch_size if batch_size and batch_size > 0 else len(refs)
    problems_by_id: dict[str, Problem] = {}
    any_batch = False
    for batch_refs in _batches(refs, size):
        batch_texts = {ref: source_texts[ref] for ref in batch_refs}
        batch_urls = {ref: urls.get(ref, "") for ref in batch_refs}
        draft = assess_problems(runner, render_candidates_json(batch_texts, batch_urls))
        if draft.eval_failed:
            # 失败不写 DiscoveryRun，下次同指纹仍会重试。
            return None
        any_batch = True
        for pd in draft.problems:
            problem = build_problem(pd, source_texts=batch_texts, urls=batch_urls, now=clock)
            if problem is not None:
                problem_repo.upsert(problem)
                problems_by_id[problem.problem_id] = problem

    if not any_batch:
        return None

    problems = list(problems_by_id.values())
    run_repo.upsert(
        DiscoveryRun(
            run_id=f"run_{clock:%Y%m%d%H%M%S}",
            created_at=clock,
            fingerprint=fingerprint,
            problem_ids=[p.problem_id for p in problems],
        )
    )
    return problems
