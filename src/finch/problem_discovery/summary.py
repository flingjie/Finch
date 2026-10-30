"""问题发现的确定性概览：为 Codex/Skill 提供可呈现的结构化结果摘要。

与 products 的「LLM 只做语义抽取、确定性代码算总数」一致：这里只算概览与计数
（卡片数 / 来源数 / 来源范围 / 缺失字段），不做重点问题选择或排序；那部分由
``skills/problem-discovery`` 的 Skill（LLM）完成。缺失信息显式表达，防止模型
补齐不存在的事实。
"""

from __future__ import annotations

from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from finch.problem_discovery.discover import _collect_sources
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.sources.store import ArtifactRepository

_SCOPE_NONE = "none"
_SCOPE_SINGLE_REPO = "single_repo"
_SCOPE_MIXED = "mixed"


class ProblemSummary(BaseModel):
    """一次问题发现概览（供 Skill 呈现；不含总分/排名/重点选择）。"""

    total_cards: int = 0
    total_sources: int = 0  # 被证据引用的独立来源数（evidence.source_ref 并集）
    source_urls: list[str] = Field(default_factory=list)  # 上述来源的去重 URL
    source_scope: str = _SCOPE_NONE  # none | single_repo | mixed
    cards_without_evidence: list[str] = Field(default_factory=list)  # 缺证据的 problem_id
    cards_without_role: list[str] = Field(default_factory=list)  # user_type 为空的 problem_id
    latest_run_created_at: str = ""
    latest_run_card_count: int = 0
    groups_available: bool = False  # 问题组尚未合并（P2 落地后置 True）


def summarize_source_scope(urls: list[str]) -> str:
    """从 URL 归并 scope：同仓→single_repo；多仓/多平台→mixed；空→none。"""
    repos: set[str] = set()
    has_other = False
    for url in urls:
        stripped = url.strip()
        if not stripped:
            continue
        parts = urlsplit(stripped)
        if parts.netloc in ("github.com", "www.github.com"):
            seg = [s for s in parts.path.split("/") if s]
            if len(seg) >= 2:
                repos.add(f"{seg[0]}/{seg[1]}")
        else:
            has_other = True
    if not repos and not has_other:
        return _SCOPE_NONE
    if len(repos) == 1 and not has_other:
        return _SCOPE_SINGLE_REPO
    return _SCOPE_MIXED


def build_summary(
    *,
    problem_repo: ProblemRepository,
    artifact_repo: ArtifactRepository,
    discussion_repo: DiscussionRepository,
    run_repo: DiscoveryRunRepository,
) -> ProblemSummary:
    """确定性汇总问题发现结果；只读，不调用 LLM。"""
    problems = problem_repo.list_all()
    _, urls = _collect_sources(artifact_repo, discussion_repo)

    # 来源范围只算被卡片证据引用的来源（不是全量已导入工件池），避免把无关仓库算进去。
    cited_refs = list(
        dict.fromkeys(e.source_ref for p in problems for e in p.evidence if e.source_ref.strip())
    )
    source_urls = list(
        dict.fromkeys(
            urls[ref].strip() for ref in cited_refs if ref in urls and urls[ref].strip()
        )
    )
    cards_without_evidence = [p.problem_id for p in problems if not p.evidence]
    cards_without_role = [p.problem_id for p in problems if not p.user_type.strip()]

    latest = run_repo.latest()
    latest_created_at = ""
    latest_card_count = 0
    if latest is not None:
        latest_created_at = latest.created_at.isoformat()
        latest_card_count = len(latest.problem_ids)

    return ProblemSummary(
        total_cards=len(problems),
        total_sources=len(cited_refs),
        source_urls=source_urls,
        source_scope=summarize_source_scope(source_urls),
        cards_without_evidence=cards_without_evidence,
        cards_without_role=cards_without_role,
        latest_run_created_at=latest_created_at,
        latest_run_card_count=latest_card_count,
        groups_available=False,
    )
