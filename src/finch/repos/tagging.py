"""Agent 主题轻量标记（设计 §6）：关键词匹配，不读 README/代码。"""

from __future__ import annotations

from finch.repos.models import RepoRecord, TopicTag, TweetRecord


def tag_repo(
    repo: RepoRecord,
    tweets: list[TweetRecord],
    *,
    keywords: list[str],
    enabled: bool = True,
) -> TopicTag:
    """对仓库打 agent / other / unknown 标签。"""
    if not enabled or not keywords:
        return TopicTag.UNKNOWN
    needles = [k.lower() for k in keywords if k.strip()]
    haystacks: list[str] = [
        repo.key,
        repo.name,
        repo.owner,
        repo.description or "",
        " ".join(repo.topics),
    ]
    for tw in tweets:
        if tw.tweet_id in repo.tweet_ids:
            haystacks.append(tw.text)
    blob = " ".join(haystacks).lower()
    if not blob.strip():
        return TopicTag.UNKNOWN
    if any(n in blob for n in needles):
        return TopicTag.AGENT
    return TopicTag.OTHER
