"""聚合相关 Commit 为工程事件候选（spec 2.2 / Phase 2）。"""

import re

from .models import CommitDetail

# 4+ 字符的弱信号词；3 字符常见动词（add/fix/use/new）由正则长度自然排除。
_STOPWORDS = {
    "with", "from", "into", "this", "that", "they", "were", "have", "will",
    "your", "more", "some", "also", "been", "phase",
}


def _significant_words(msg: str) -> set[str]:
    """从消息主题提取有效词（去掉类型前缀、停用词与过短词）。"""
    body = msg.partition(":")[2] if ":" in msg else msg
    words = re.findall(r"[a-z][a-z0-9-]{3,}", body.lower())
    return {w for w in words if w not in _STOPWORDS}


def group_commits(commits: list[CommitDetail],
                  *,
                  window_minutes: int = 90) -> list[list[CommitDetail]]:
    """把时间窗口内、共享有效词或文件路径的 Commit 聚合为工程事件候选。"""
    ordered = sorted(commits, key=lambda c: c.author_date)
    groups: list[list[CommitDetail]] = []
    for c in ordered:
        placed = False
        for g in groups:
            head = g[-1]
            if _within_window(head, c, window_minutes) and _related(head, c):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])
    return groups


def _within_window(a: CommitDetail, b: CommitDetail, minutes: int) -> bool:
    return abs((a.author_date - b.author_date).total_seconds()) / 60.0 <= minutes


def _related(a: CommitDetail, b: CommitDetail) -> bool:
    if _significant_words(a.message) & _significant_words(b.message):
        return True
    pa = {f.filename for f in a.files}
    pb = {f.filename for f in b.files}
    return bool(pa & pb)
