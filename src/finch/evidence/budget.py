"""每日预算选择：确定性排序信号 + rank_pending / select_groups（阶段 A）。"""

import re
from datetime import datetime
from math import log1p

from ..github.models import CommitDetail, CommitSummary
from ..settings import DailyBudget
from .extractor import _render_commits

_STOPWORDS = {
    "with", "from", "into", "this", "that", "they", "were", "have", "will",
    "your", "more", "some", "also", "been", "phase",
}

_KEYWORDS = ("fix", "refactor", "perf", "performance", "architect", "feat", "optimiz")

_LOCKFILE_MARKERS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
                     "Cargo.lock", "Gemfile.lock", "go.sum", "uv.lock")
_DEP_MARKERS = (*_LOCKFILE_MARKERS, "requirements", "package.json", "pyproject.toml",
                "go.mod", "cargo.toml", "composer.json", "gemfile")
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")
_TEST_MARKERS = ("test", "spec", "__tests__")

_CROSS_MODULE_FULL = 3
_CHURN_LOG_NORM = log1p(1000)


def _significant_words(msg: str) -> set[str]:
    body = msg.partition(":")[2] if ":" in msg else msg
    words = re.findall(r"[a-z][a-z0-9-]{3,}", body.lower())
    return {w for w in words if w not in _STOPWORDS}


def _path_tokens(files: list) -> set[str]:
    tokens: set[str] = set()
    for f in files:
        for part in f.filename.lower().split("/"):
            if part:
                tokens.add(part)
    return tokens


def _is_doc(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(_DOC_SUFFIXES) or lower.startswith("docs/") or "readme" in lower


def _is_test(path: str) -> bool:
    return any(m in path.lower() for m in _TEST_MARKERS)


def _is_dep(path: str) -> bool:
    return any(m in path.lower() for m in _DEP_MARKERS)


def core_source_ratio(files: list) -> float:
    """改核心源码（非 doc/test/dep）的文件占比。"""
    if not files:
        return 0.0
    src = sum(
        1 for f in files
        if not (_is_doc(f.filename) or _is_test(f.filename) or _is_dep(f.filename))
    )
    return src / len(files)


def churn_score(files: list) -> float:
    """Σ(additions+deletions) 的对数尺度归一。"""
    total = sum(f.additions + f.deletions for f in files)
    return min(log1p(total) / _CHURN_LOG_NORM, 1.0)


def keyword_score(message: str) -> float:
    return 1.0 if any(k in message.lower() for k in _KEYWORDS) else 0.0


def cross_module_score(files: list) -> float:
    """跨模块展开度：distinct 顶层目录数（3+ 记满分）。"""
    top_dirs = {f.filename.split("/", 1)[0] for f in files if "/" in f.filename}
    return min(len(top_dirs) / _CROSS_MODULE_FULL, 1.0)


def novelty_score(tokens: set[str], existing_topics: set[str]) -> float:
    """1 − 与既有 EvidenceCard.topics 的最大 Jaccard；无历史记 1.0。"""
    if not tokens or not existing_topics:
        return 1.0
    best = 0.0
    for topic in existing_topics:
        topic_tokens = set(topic.lower().split())
        inter = len(tokens & topic_tokens)
        if inter:
            best = max(best, inter / len(tokens | topic_tokens))
    return 1.0 - best


def age_bonus(discovered_at: datetime, now: datetime, max_days: int) -> float:
    age_days = (now - discovered_at).total_seconds() / 86400.0
    return min(max(age_days, 0.0) / max_days, 1.0)


def _group_signals(
    group: list[CommitDetail], existing_topics: set[str]
) -> tuple[float, float, float, float, float]:
    files = [f for c in group for f in c.files]
    core = core_source_ratio(files)
    churn = churn_score(files)
    keyword = 1.0 if any(keyword_score(c.message) for c in group) else 0.0
    cross = cross_module_score(files)
    tokens: set[str] = set()
    for c in group:
        tokens |= _significant_words(c.message)
        tokens |= _path_tokens(c.files)
    novelty = novelty_score(tokens, existing_topics)
    return core, churn, keyword, cross, novelty


def _score(core, churn, keyword, cross, novelty, age, weights) -> float:
    return (
        weights.core_source * core
        + weights.churn * churn
        + weights.keyword * keyword
        + weights.cross_module * cross
        + weights.novelty * novelty
        + weights.age_bonus * age
    )


def select_groups(
    groups: list[list[CommitDetail]],
    existing_topics: set[str],
    settings: DailyBudget,
    discovered_at: dict[str, datetime],
    now: datetime,
) -> list[list[CommitDetail]]:
    """确定性选出本轮提取预算内的 group（score 降序，受 group 数与估算字节双约束）。"""
    scored: list[tuple[float, int, list[CommitDetail]]] = []
    for i, group in enumerate(groups):
        core, churn, keyword, cross, novelty = _group_signals(group, existing_topics)
        oldest = min((discovered_at.get(c.sha, now) for c in group), default=now)
        age = age_bonus(oldest, now, settings.age_bonus_max_days)
        s = _score(core, churn, keyword, cross, novelty, age, settings.sort_weights)
        scored.append((s, i, group))
    scored.sort(key=lambda t: (-t[0], t[1]))

    selected: list[list[CommitDetail]] = []
    est_bytes = 0
    for _s, _i, group in scored:
        if len(selected) >= settings.max_change_groups:
            break
        group_bytes = len(_render_commits(group).encode("utf-8"))
        if est_bytes + group_bytes > settings.max_estimated_prompt_bytes:
            continue
        selected.append(group)
        est_bytes += group_bytes
    return selected


def rank_pending(
    items: list[tuple[CommitSummary, datetime]],
    existing_topics: set[str],
    settings: DailyBudget,
    now: datetime,
) -> list[CommitSummary]:
    """段 1 预排序：用 summary 级信号（无文件级）给 pending 排序，供 detail-fetch 选择。"""
    scored: list[tuple[float, int, CommitSummary]] = []
    for i, (summary, discovered) in enumerate(items):
        kw = keyword_score(summary.message)
        novelty = novelty_score(_significant_words(summary.message), existing_topics)
        age = age_bonus(discovered, now, settings.age_bonus_max_days)
        w = settings.sort_weights
        s = w.keyword * kw + w.novelty * novelty + w.age_bonus * age
        scored.append((s, i, summary))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [summary for _s, _i, summary in scored]
