"""相关热门帖子选择：相关优先，热度作为同相关性下的次级信号。

输入是已经通过 ``filter_artifacts`` 的帖子。相关度来自用户当前问题/本轮意图的词命中，
以及长期兴趣与探索方向的确定性子串命中；热度按平台可用的公开互动指标归一化。
与人物推荐一致，热度不单独决定排序。
"""

from __future__ import annotations

import re

from finch.engagement.models import HotPostEntry
from finch.settings import Settings
from finch.sources.models import RawArtifact


def _tokens(text: str) -> list[str]:
    """从一句意图中提取可匹配词；英文取完整词，中文取连续串或二字片段。"""
    lowered = (text or "").lower()
    out: list[str] = []
    out.extend(re.findall(r"[a-z0-9_]{3,}", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) <= 8:
            out.append(run)
        else:
            out.extend(run[i : i + 2] for i in range(len(run) - 1))
    return out


def _terms(settings: Settings, question: str) -> list[tuple[str, float]]:
    """收集确定性相关词：长期兴趣 / 探索方向 / 相邻查询 + 当前意图词。"""
    terms: dict[str, float] = {}

    def add(term: str, weight: float) -> None:
        clean = (term or "").strip().lower()
        if len(clean) < 3:
            return
        terms[clean] = max(terms.get(clean, 0.0), weight)

    for term in settings.interests.long_term_interests:
        add(term, 2.0)
    for term in settings.interests.explore_directions:
        add(term, 1.0)
    for term in settings.interests.adjacent_queries:
        add(term, 1.0)
    for token in _tokens(question):
        add(token, 3.0)
    return [(term, weight) for term, weight in terms.items()]


def _raw_heat(art: RawArtifact) -> float:
    """按平台可获取的公开互动指标估算热度；不同平台只做平台内归一化。"""
    platform = art.author_identity.platform
    metrics = art.metrics or {}

    def num(*keys: str) -> float:
        for key in keys:
            value = metrics.get(key)
            if isinstance(value, (int, float)):
                return float(value)
        return 0.0

    if platform == "x":
        return num("likes") * 3.0 + num("views") * 0.01
    if platform == "reddit":
        return num("score") + num("comments") * 2.0
    if platform == "v2ex":
        return num("replies")
    if platform == "xiaohongshu":
        return num("likes", "liked_count")
    return 0.0


def _text(art: RawArtifact) -> str:
    return "\n".join(p for p in [art.title or "", art.text] if p).strip().lower()


def _title(art: RawArtifact) -> str:
    title = (art.title or "").strip()
    if title:
        return title[:120]
    first_line = (art.text or "").strip().splitlines()
    return (first_line[0] if first_line else "")[:120]


def select_hot_posts(
    artifacts: list[RawArtifact],
    *,
    settings: Settings,
    question: str = "",
    limit: int = 5,
) -> list[HotPostEntry]:
    """从已过滤帖子中确定性选择最多 ``limit`` 条相关热门帖子。"""
    terms = _terms(settings, question)
    if not artifacts or limit <= 0:
        return []

    per_platform_max: dict[str, float] = {}
    rows: list[dict] = []
    for art in artifacts:
        platform = art.author_identity.platform
        blob = _text(art)
        matched_terms: list[str] = []
        relevance = 0.0
        for term, weight in terms:
            if term in blob:
                matched_terms.append(term)
                relevance += weight
        heat = _raw_heat(art)
        if heat > 0:
            per_platform_max[platform] = max(
                per_platform_max.get(platform, 0.0), heat
            )
        rows.append(
            {
                "art": art,
                "platform": platform,
                "relevance": relevance,
                "heat": heat,
                "matched_terms": matched_terms,
            }
        )

    max_relevance = max((row["relevance"] for row in rows), default=0.0)
    for row in rows:
        platform_max = per_platform_max.get(row["platform"], 0.0)
        row["relevance_score"] = (
            row["relevance"] / max_relevance if max_relevance else 0.0
        )
        row["heat_score"] = row["heat"] / platform_max if platform_max else 0.0

    # 相关优先；同一相关性下热度更高、更新鲜、id 更小者优先，保证可重放。
    rows.sort(
        key=lambda row: (
            -row["relevance_score"],
            -row["heat_score"],
            -(row["art"].published_at or row["art"].retrieved_at).timestamp(),
            row["art"].artifact_id,
        )
    )

    out: list[HotPostEntry] = []
    for row in rows[:limit]:
        art = row["art"]
        out.append(
            HotPostEntry(
                artifact_id=art.artifact_id,
                title=_title(art),
                url=art.canonical_url,
                platform=art.author_identity.platform,
                author=art.author_identity.handle or art.author_identity.external_id,
                published_at=art.published_at or art.retrieved_at,
                relevance_score=round(row["relevance_score"], 4),
                heat_score=round(row["heat_score"], 4),
                matched_terms=row["matched_terms"][:6],
            )
        )
    return out
