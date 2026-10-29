"""产品用户反馈研究：定向搜索 → 过滤 → LLM 抽取 → 确定性门禁 → 报告。

原则：用户反馈是证据，不是投票。LLM 只做语义抽取，代码负责归属、去重、证据门禁、
摘录可追溯（``original_excerpt`` 必须是候选文本子串）与缓存/失效。没有足够反馈时
返回 ``insufficient_feedback``，不编造。

复用 ``sources`` 的 connector（Reddit / X）与 ``GitHubConnector.fetch_repo_feedback``；
不新增 Graph Runtime / Agent 框架 / 数据库。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from finch.llm.base import StructuredInferenceRunner
from finch.products.models import (
    EvidenceStatus,
    ExperienceLevel,
    Feedback,
    FeedbackPoint,
    FeedbackReport,
    FeedbackType,
    Product,
)
from finch.products.repository import FeedbackRepository
from finch.settings import Settings
from finch.sources.connectors import DiscoveryContext
from finch.sources.connectors.github import GitHubConnector
from finch.sources.models import RawArtifact, Source
from finch.sources.orchestrator import DiscoveryOrchestrator

_PROMPT = Path("prompts/product-feedback.md")
FEEDBACK_VERSION = "product-feedback-v1"
_TEXT_LIMIT = 2000

# 自宣/营销口吻（不是真实使用反馈）。
_PROMO_MARKERS = (
    "i built",
    "we built",
    "i made",
    "we made",
    "i launched",
    "we launched",
    "just launched",
    "just shipped",
    "show hn",
    "product hunt",
    "我做了",
    "我开发了",
    "我们发布了",
    "我们做了",
)

_STOPWORDS = frozenset(
    {"the", "a", "an", "for", "with", "and", "or", "to", "of", "in", "on", "by",
     "app", "product", "tool", "ai", "new", "this", "that"}
)

_FIRST_HAND_RE = re.compile(
    r"\bi\b|\bi'm\b|\bi've\b|\bmy\b|\btried\b|\busing\b|\bswitched\b|我|我们"
)


class FeedbackItemDraft(BaseModel):
    """LLM 抽取的单条反馈（enum 用 str 宽容解析，代码再强制纠正）。"""

    source_url: str = ""
    author: str = ""
    feedback_type: str = "question"
    user_scenario: str = ""
    original_excerpt: str = ""
    user_problem: str = ""
    requested_outcome: str = ""
    experience_level: str = "unknown"
    evidence_status: str = "assumption"
    confidence: str = "medium"
    product_match: bool = True


class FeedbackPointDraft(BaseModel):
    claim: str = ""
    feedback_refs: list[int] = Field(default_factory=list)  # 候选讨论 index
    status: str = "assumption"


class FeedbackExtraction(BaseModel):
    """一次 LLM 抽取调用返回：逐条反馈 + 报告综合字段。"""

    feedback: list[FeedbackItemDraft] = Field(default_factory=list)
    recurring_problems: list[FeedbackPointDraft] = Field(default_factory=list)
    unmet_needs: list[FeedbackPointDraft] = Field(default_factory=list)
    contradictions: list[FeedbackPointDraft] = Field(default_factory=list)
    questions_for_founder: list[FeedbackPointDraft] = Field(default_factory=list)
    info_gaps: str = ""
    eval_failed: bool = False
    skip_reason: str = ""


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _normalize(text: str) -> str:
    return " ".join((text or "").split()).lower()


def _fingerprint(obj: object) -> str:
    payload = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _feedback_id(product_id: str, source_value: str, source_url: str, excerpt: str) -> str:
    payload = "|".join([product_id, source_value, source_url, _normalize(excerpt)])
    return f"fb_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _report_id(product_id: str, version: str, feedback_ids: list[str]) -> str:
    payload = "|".join([product_id, version, *sorted(feedback_ids)])
    return f"fbr_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"


def _product_name(product: Product) -> str:
    name = (product.name or "").strip()
    if name:
        return name
    key = (product.key or "").strip()
    return key.rstrip("/").split("/")[-1]


def _attribution_tokens(product: Product) -> set[str]:
    tokens: set[str] = set()
    for word in re.split(r"[^a-z0-9]+", (product.name or "").lower()):
        if len(word) >= 3 and word not in _STOPWORDS:
            tokens.add(word)
    key = (product.key or "").strip().lower()
    if key:
        tokens.add(key)
        segment = key.rstrip("/").split("/")[-1]
        if segment and segment not in _STOPWORDS:
            tokens.add(segment)
    url = (product.product_url or "").strip().lower()
    if url:
        host = urlparse(url).hostname or ""
        if host:
            tokens.add(host)
    return {t for t in tokens if t}


def _github_repo(product: Product) -> str | None:
    """从 key / product_url 提取 owner/repo；非 GitHub 产品返回 None。"""
    key = (product.key or "").strip().lower()
    if key.startswith("github.com/"):
        repo = key[len("github.com/") :].strip("/")
        if "/" in repo:
            return repo
    url = (product.product_url or "").strip().lower()
    prefix = "https://github.com/"
    if url.startswith(prefix):
        repo = url[len(prefix) :].strip("/")
        if "/" in repo:
            return repo
    return None


def _founder_handles(product: Product) -> set[str]:
    out: set[str] = set()
    founder = product.founder
    if founder is None:
        return out
    for handle in founder.handles:
        for ident in (handle.handle, handle.external_id):
            if ident:
                out.add(ident.strip().lower())
    return out


def build_feedback_queries(product: Product) -> dict[Source, list[str]]:
    """由产品名/别名构造 Reddit / X 定向搜索查询（GitHub 走仓库抓取，不查词）。"""
    name = _product_name(product)
    if not name:
        return {}
    queries = [name, f"{name} review", f"{name} complain", f"{name} alternative"]
    return {Source.REDDIT: list(queries), Source.TWITTER: list(queries)}


def _matches_product(text: str, url: str, tokens: set[str]) -> bool:
    if not tokens:
        return True
    hay = f"{text}\n{url}".lower()
    return any(t in hay for t in tokens)


def _is_founder(art: RawArtifact, founder: set[str]) -> bool:
    ident = (art.author_identity.handle or art.author_identity.external_id or "").strip().lower()
    return bool(ident) and ident in founder


def _is_promo(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in _PROMO_MARKERS)


def filter_feedback_candidates(
    artifacts: list[RawArtifact], product: Product
) -> list[RawArtifact]:
    """确定性过滤：归属 → 去宣传 → 跨源去重。返回有效反馈候选。"""
    tokens = _attribution_tokens(product)
    founder = _founder_handles(product)
    kept: list[RawArtifact] = []
    seen_fp: set[str] = set()
    seen_url: set[str] = set()
    for art in artifacts:
        text = f"{art.title or ''}\n{art.text}"
        if art.source != Source.GITHUB and not _matches_product(text, art.canonical_url, tokens):
            continue
        if _is_founder(art, founder) or _is_promo(text):
            continue
        fp = art.content_fingerprint
        url = (art.canonical_url or "").strip()
        if (fp and fp in seen_fp) or (url and url in seen_url):
            continue
        if fp:
            seen_fp.add(fp)
        if url:
            seen_url.add(url)
        kept.append(art)
    return kept


def render_candidates_json(candidates: list[RawArtifact]) -> str:
    rows = [
        {
            "index": i,
            "platform": c.source.value,
            "url": c.canonical_url,
            "author": c.author_identity.handle or c.author_identity.external_id,
            "published_at": c.published_at.isoformat() if c.published_at else "",
            "text": _truncate(f"{c.title or ''}\n{c.text}"),
        }
        for i, c in enumerate(candidates)
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _product_payload(product: Product) -> str:
    return json.dumps(
        {
            "name": product.name,
            "key": product.key,
            "product_url": product.product_url,
            "founder": product.founder.display_name if product.founder else "",
            "target_user": product.target_user,
            "problem_solved": product.problem_solved,
            "core_solution": product.core_solution,
        },
        ensure_ascii=False,
        indent=2,
    )


def assess_feedback(
    runner: StructuredInferenceRunner, product: Product, candidates: list[RawArtifact]
) -> FeedbackExtraction:
    """LLM 抽取反馈 + 综合；失败 → eval_failed（fail-soft）。"""
    prompt = _PROMPT.read_text().format(
        product_json=_product_payload(product),
        candidates_json=render_candidates_json(candidates),
    )
    try:
        return cast(FeedbackExtraction, runner.run(prompt, FeedbackExtraction))
    except Exception:
        return FeedbackExtraction(eval_failed=True, skip_reason="反馈抽取失败（LLM 异常）")


def _coerce_feedback_type(value: str) -> FeedbackType:
    try:
        return FeedbackType(value)
    except ValueError:
        return FeedbackType.QUESTION


def _coerce_evidence(value: str) -> EvidenceStatus:
    try:
        return EvidenceStatus(value)
    except ValueError:
        return EvidenceStatus.ASSUMPTION


def _coerce_confidence(value: str) -> Literal["low", "medium", "high"]:
    if value in ("low", "medium", "high"):
        return cast(Literal["low", "medium", "high"], value)
    return "medium"


def _has_first_hand(text: str) -> bool:
    return bool(_FIRST_HAND_RE.search(text))


def _coerce_experience(value: str, excerpt_norm: str) -> ExperienceLevel:
    if value == "first_hand" and not _has_first_hand(excerpt_norm):
        return ExperienceLevel.UNKNOWN
    try:
        return ExperienceLevel(value)
    except ValueError:
        return ExperienceLevel.UNKNOWN


def _find_candidate(excerpt_norm: str, cand_texts: list[str]) -> int | None:
    for idx, text in enumerate(cand_texts):
        if excerpt_norm and excerpt_norm in text:
            return idx
    return None


def build_feedback_report(
    extraction: FeedbackExtraction,
    product: Product,
    candidates: list[RawArtifact],
    *,
    version: str,
    search_fingerprint: str,
) -> FeedbackReport:
    """确定性把 LLM 抽取转为报告：摘录可追溯、链接可追溯、refs 子集、无反馈即 insufficient。"""
    if extraction.eval_failed:
        return FeedbackReport(
            report_id=_report_id(product.id, version, []),
            product_id=product.id,
            version=version,
            insufficient_feedback=True,
            search_fingerprint=search_fingerprint,
            info_gaps=extraction.skip_reason or "反馈抽取失败",
        )

    cand_texts = [_normalize(f"{c.title or ''}\n{c.text}") for c in candidates]
    feedback: list[Feedback] = []
    feedback_ids: set[str] = set()
    cand_to_ids: dict[int, list[str]] = {}
    for item in extraction.feedback:
        if not item.product_match:
            continue
        excerpt_norm = _normalize(item.original_excerpt)
        if not excerpt_norm:
            continue
        idx = _find_candidate(excerpt_norm, cand_texts)
        if idx is None:
            continue  # 摘录无法回溯到候选 → 丢弃（防伪造）
        match = candidates[idx]
        fid = _feedback_id(
            product.id, match.source.value, match.canonical_url, item.original_excerpt
        )
        feedback.append(
            Feedback(
                feedback_id=fid,
                product_id=product.id,
                source=match.source,
                source_url=match.canonical_url,
                author=(item.author or "").strip()
                or (match.author_identity.handle or match.author_identity.external_id),
                published_at=match.published_at,
                feedback_type=_coerce_feedback_type(item.feedback_type),
                user_scenario=(item.user_scenario or "").strip(),
                original_excerpt=item.original_excerpt.strip(),
                user_problem=(item.user_problem or "").strip(),
                requested_outcome=(item.requested_outcome or "").strip(),
                experience_level=_coerce_experience(item.experience_level, excerpt_norm),
                evidence_status=_coerce_evidence(item.evidence_status),
                confidence=_coerce_confidence(item.confidence),
            )
        )
        feedback_ids.add(fid)
        cand_to_ids.setdefault(idx, []).append(fid)

    def _points(drafts: list[FeedbackPointDraft]) -> list[FeedbackPoint]:
        out: list[FeedbackPoint] = []
        for d in drafts:
            refs: list[str] = []
            for idx in d.feedback_refs:
                refs.extend(cand_to_ids.get(idx, []))
            refs = list(dict.fromkeys(refs))  # 去重保序
            status = _coerce_evidence(d.status) if refs else EvidenceStatus.ASSUMPTION
            out.append(
                FeedbackPoint(
                    claim=(d.claim or "").strip(),
                    feedback_refs=refs,
                    count=len(refs),
                    status=status,
                )
            )
        return out

    report = FeedbackReport(
        report_id=_report_id(product.id, version, sorted(feedback_ids)),
        product_id=product.id,
        version=version,
        feedback=feedback,
        recurring_problems=_points(extraction.recurring_problems),
        unmet_needs=_points(extraction.unmet_needs),
        contradictions=_points(extraction.contradictions),
        questions_for_founder=_points(extraction.questions_for_founder),
        info_gaps=(extraction.info_gaps or "").strip(),
        insufficient_feedback=not feedback,
        search_fingerprint=search_fingerprint,
        sources=sorted({f.source.value for f in feedback}),
    )
    return report


def _search_artifacts(
    product: Product,
    settings: Settings,
    orchestrator: DiscoveryOrchestrator | None,
    github_connector: GitHubConnector | None,
) -> list[RawArtifact]:
    cfg = settings.products.feedback
    artifacts: list[RawArtifact] = []
    queries = build_feedback_queries(product)
    limit = min(cfg.max_candidates, 30)
    for source in (Source.REDDIT, Source.TWITTER):
        if source not in cfg.sources or source not in queries or orchestrator is None:
            continue
        ctx = DiscoveryContext(queries=queries[source], limit=limit)
        artifacts.extend(orchestrator.sync_source(source, ctx, project_peers=False).artifacts)
    if Source.GITHUB in cfg.sources and github_connector is not None:
        repo = _github_repo(product)
        if repo:
            artifacts.extend(github_connector.fetch_repo_feedback(repo))
    return artifacts


def _is_stale(report: FeedbackReport, stale_hours: int, clock: datetime) -> bool:
    if stale_hours <= 0:
        return False
    return report.created_at < clock - timedelta(hours=stale_hours)


def research_feedback(
    product: Product,
    *,
    runner: StructuredInferenceRunner | None,
    repository: FeedbackRepository,
    settings: Settings,
    orchestrator: DiscoveryOrchestrator | None = None,
    github_connector: GitHubConnector | None = None,
    force: bool = False,
    now: datetime | None = None,
) -> FeedbackReport | None:
    """研究一个产品的用户反馈；幂等（同 search_fingerprint + 未过期复用缓存），``force`` 重搜。"""
    cfg = settings.products.feedback
    if not cfg.enabled:
        return None
    version = cfg.version
    queries = build_feedback_queries(product)
    search_fp = _fingerprint(
        {
            "product": [product.key, product.name, product.product_url],
            "queries": {s.value: q for s, q in queries.items()},
            "sources": sorted(s.value for s in cfg.sources),
            "version": version,
        }
    )
    clock = now or datetime.now(UTC)
    if not force:
        existing = repository.get(product.id)
        if (
            existing is not None
            and existing.search_fingerprint == search_fp
            and not _is_stale(existing, cfg.stale_hours, clock)
        ):
            return existing

    artifacts = _search_artifacts(product, settings, orchestrator, github_connector)
    candidates = filter_feedback_candidates(artifacts, product)
    repository.clear_raw(product.id)
    for c in candidates:
        repository.append_raw(
            product.id,
            {
                "platform": c.source.value,
                "url": c.canonical_url,
                "author": c.author_identity.handle or c.author_identity.external_id,
                "published_at": c.published_at.isoformat() if c.published_at else "",
                "excerpt": _truncate(f"{c.title or ''}\n{c.text}"),
            },
        )

    if not candidates:
        report = FeedbackReport(
            report_id=_report_id(product.id, version, []),
            product_id=product.id,
            version=version,
            insufficient_feedback=True,
            search_fingerprint=search_fp,
            info_gaps="未找到与该产品相关的候选反馈",
        )
        repository.upsert(report)
        return report
    if runner is None:
        report = FeedbackReport(
            report_id=_report_id(product.id, version, []),
            product_id=product.id,
            version=version,
            insufficient_feedback=True,
            search_fingerprint=search_fp,
            info_gaps="无 LLM runner，跳过反馈抽取",
        )
        repository.upsert(report)
        return report

    extraction = assess_feedback(runner, product, candidates)
    report = build_feedback_report(
        extraction, product, candidates, version=version, search_fingerprint=search_fp
    )
    repository.upsert(report)
    return report
