"""确定性硬过滤：在调用 LLM 前去掉明显噪声，每个淘汰候选保留 reason_code。

复用 ``content_fingerprint`` 与 ``engagement.scoring._is_repost`` 的转发启发式，
接入 ``interests.excluded_content`` 作为硬排除词。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from finch.sources.models import RawArtifact

# Reason codes (deterministic; also feed shortfall reporting in Phase 3).
EMPTY_CONTENT = "empty_content"
NO_AUTHOR = "no_author"
INVALID_PLATFORM = "invalid_platform"
REPOST = "repost"
EXCLUDED_CONTENT = "excluded_content"
DUPLICATE = "duplicate"
TOO_OLD = "too_old"

# 与 engagement/scoring.py 的 _REPOST_PREFIXES 保持一致。
_REPOST_PREFIXES = ("rt @", "rt@", "repost", "转", "转发")

_VALID_PLATFORMS = frozenset(
    {"x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"}
)


@dataclass(frozen=True)
class FilterDecision:
    """单个 artifact 的过滤结论。"""

    artifact: RawArtifact
    keep: bool
    reason_code: str = ""


def artifact_text(art: RawArtifact) -> str:
    """artifact 的可读正文（title + text 合并，供过滤/命中判断）。"""
    return "\n".join(p for p in [art.title or "", art.text] if p).strip()


def is_repost(text: str) -> bool:
    """纯转发/引用启发式：空内容、转发前缀、单链接。"""
    stripped = text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    if any(lowered.startswith(prefix) for prefix in _REPOST_PREFIXES):
        return True
    if stripped.startswith(("http://", "https://")) and " " not in stripped:
        return True
    return False


def matches_excluded(text: str, excluded_content: list[str]) -> bool:
    """大小写不敏感子串匹配排除词。"""
    lowered = text.lower()
    return any(term.lower() in lowered for term in excluded_content if term.strip())


def filter_artifacts(
    artifacts: list[RawArtifact],
    *,
    excluded_content: list[str] | None = None,
    min_content_length: int = 20,
    lookback_hours: int | None = None,
    as_of: datetime | None = None,
) -> tuple[list[RawArtifact], dict[str, int]]:
    """硬过滤 + 指纹去重，返回 ``(kept, reason_counts)``。

    - ``lookback_hours`` 为 None 时不按时间过滤（默认关闭）。
    - 未来时间戳（``published > as_of``）被钳制为 ``as_of``：不误伤也不虚增新鲜度。
    - 内容长度/身份/来源/排除词/转发/重复各对应一个确定 reason_code。
    """
    excluded = list(excluded_content or [])
    kept: list[RawArtifact] = []
    counts: dict[str, int] = {}
    seen_fingerprints: set[str] = set()

    if lookback_hours is not None:
        from datetime import UTC, timedelta

        clock = as_of or datetime.now(UTC)
        cutoff = clock - timedelta(hours=lookback_hours)
    else:
        cutoff = None

    def _reject(code: str) -> None:
        counts[code] = counts.get(code, 0) + 1

    for art in artifacts:
        platform = (art.author_identity.platform or "").strip()
        if platform not in _VALID_PLATFORMS:
            _reject(INVALID_PLATFORM)
            continue
        author_id = (
            art.author_identity.external_id or art.author_identity.handle or ""
        ).strip()
        if not author_id:
            _reject(NO_AUTHOR)
            continue
        text = artifact_text(art)
        if len(text) < min_content_length:
            _reject(EMPTY_CONTENT)
            continue
        if is_repost(text):
            _reject(REPOST)
            continue
        if matches_excluded(text, excluded):
            _reject(EXCLUDED_CONTENT)
            continue
        if cutoff is not None:
            published = art.published_at or art.retrieved_at
            if published is not None:
                effective = published if published <= clock else clock
                if effective < cutoff:
                    _reject(TOO_OLD)
                    continue
        fp = art.content_fingerprint
        if fp and fp in seen_fingerprints:
            _reject(DUPLICATE)
            continue
        if fp:
            seen_fingerprints.add(fp)
        kept.append(art)

    return kept, counts
