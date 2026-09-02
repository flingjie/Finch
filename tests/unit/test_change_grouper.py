# tests/unit/test_change_grouper.py
from finch.github.change_grouper import group_commits
from finch.github.models import CommitDetail, CommitFile


def _c(sha, msg, files, t):
    return CommitDetail(sha=sha, message=msg, author_date=t, html_url="u",
                        parents=[], files=files, stats={})


def test_groups_by_message_prefix():
    commits = [
        _c("a" * 40, "feat: node-ize the four subgraphs (Phase 3 foundation)",
           [CommitFile(filename="src/graph/a.ts", status="modified")], "2026-09-01T05:19:04Z"),
        _c("b" * 40, "feat: node-ize the orchestrator (Phase 3 integration)",
           [CommitFile(filename="src/graph/b.ts", status="modified")], "2026-09-01T06:32:54Z"),
    ]
    groups = group_commits(commits)
    assert len(groups) == 1
    assert len(groups[0]) == 2


def test_does_not_group_unrelated():
    commits = [
        _c("a" * 40, "feat: add telemetry", [CommitFile(filename="src/telemetry.ts", status="modified")],
           "2026-09-01T05:00:00Z"),
        _c("b" * 40, "docs: update readme", [CommitFile(filename="README.md", status="modified")],
           "2026-09-01T07:00:00Z"),
    ]
    assert len(group_commits(commits)) == 2
