"""聚合相关 Commit 为工程事件候选（spec 2.2 / Phase 2）。"""

from datetime import datetime

from .models import CommitDetail

_PREFIX_LEN = 24


def _prefix(msg: str) -> str:
    return msg.split(":")[0].strip().rstrip(":")


def group_commits(commits: list[CommitDetail], *, window_minutes: int = 90,
                  max_files: int = 200) -> list[list[CommitDetail]]:
    ordered = sorted(commits, key=lambda c: c.author_date)
    groups: list[list[CommitDetail]] = []
    for c in ordered:
        placed = False
        for g in groups:
            head = g[-1]
            if _within_window(head, c, window_minutes) and _related(head, c, max_files):
                g.append(c)
                placed = True
                break
        if not placed:
            groups.append([c])
    return groups


def _within_window(a: CommitDetail, b: CommitDetail, minutes: int) -> bool:
    dt = abs((a.author_date - b.author_date).total_seconds()) / 60.0
    return dt <= minutes


def _related(a: CommitDetail, b: CommitDetail, max_files: int) -> bool:
    if _prefix(a.message) == _prefix(b.message):
        return True
    pa = {f.filename for f in a.files}
    pb = {f.filename for f in b.files}
    return bool(pa & pb)
