"""Tweet 规范化、去重与噪音过滤（spec 5.3 / 8）."""

import re

from .models import Tweet

# 显式广告关键词（轻量启发式）
_AD_KEYWORDS = frozenset({
    "click here", "buy now", "limited time", "discount code", "promo",
    "subscribe now", "follow & retweet", "giveaway", "free trial",
})

# 被屏蔽作者列表（运行时可配置）
_BLOCKED_HANDLES: set[str] = set()


def normalize_url(url: str) -> str:
    """规范化 x.com URL：去除跟踪参数，统一 scheme.

    opencli 当前返回 https://x.com/i/status/<id> 或 https://x.com/<handle>/status/<id>
    统一保留后者格式（若 handle 可用则替换 i）。
    """
    # 去除常见跟踪参数
    cleaned = re.sub(r"\?.*", "", url)
    # 统一为 https
    cleaned = cleaned.replace("http://", "https://")
    return cleaned


def _is_advertisement(tweet: Tweet) -> bool:
    text_lower = tweet.text.lower()
    return any(kw in text_lower for kw in _AD_KEYWORDS)


def _is_empty(tweet: Tweet) -> bool:
    return not tweet.text or not tweet.text.strip()


def _is_blocked(tweet: Tweet) -> bool:
    return tweet.author in _BLOCKED_HANDLES


def filter_noise(tweets: list[Tweet]) -> list[Tweet]:
    """确定性过滤：空文本、广告、被屏蔽作者（spec 8 第一层）."""
    result: list[Tweet] = []
    for t in tweets:
        if _is_empty(t):
            continue
        if _is_advertisement(t):
            continue
        if _is_blocked(t):
            continue
        result.append(t)
    return result


def deduplicate(tweets: list[Tweet]) -> list[Tweet]:
    """按 Tweet ID 去重；同 ID 保留首次出现（spec 5.3）."""
    seen: set[str] = set()
    result: list[Tweet] = []
    for t in tweets:
        if t.id in seen:
            continue
        seen.add(t.id)
        result.append(t)
    return result


def normalize_tweets(tweets: list[Tweet]) -> list[Tweet]:
    """完整规范化管道：去重 → 过滤."""
    return filter_noise(deduplicate(tweets))
