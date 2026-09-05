from datetime import UTC, datetime, timedelta

from finch.evidence.budget import (
    age_bonus,
    churn_score,
    core_source_ratio,
    cross_module_score,
    keyword_score,
    novelty_score,
    rank_pending,
    select_groups,
)
from finch.github.models import CommitDetail, CommitFile, CommitSummary
from finch.settings import DailyBudget

NOW = datetime(2026, 9, 5, tzinfo=UTC)


def _files(*paths):
    return [CommitFile(filename=p, status="modified", additions=10, deletions=2) for p in paths]


def _detail(sha, files, message="feat: node-ize orchestrator"):
    return CommitDetail(
        sha=sha, message=message, author_date=datetime(2026, 9, 1, tzinfo=UTC),
        html_url="u", parents=[], files=files, stats={},
    )


def test_core_source_ratio():
    assert core_source_ratio(_files("src/a.py", "docs/readme.md")) == 0.5
    assert core_source_ratio(_files("tests/test_a.py")) == 0.0
    assert core_source_ratio(_files("package-lock.json")) == 0.0
    assert core_source_ratio([]) == 0.0


def test_churn_score_monotonic():
    small = churn_score(_files("a.py"))
    big = churn_score(
        [CommitFile(filename="a.py", status="modified", additions=500, deletions=500)]
    )
    assert 0.0 < small < big <= 1.0


def test_keyword_score():
    assert keyword_score("fix: handle retry") == 1.0
    assert keyword_score("docs: update") == 0.0


def test_cross_module_score():
    assert cross_module_score(_files("src/a.py", "lib/b.py", "tests/t.py")) == 1.0
    assert cross_module_score(_files("src/a.py")) == 1.0 / 3


def test_novelty_score():
    assert novelty_score({"agent", "harness"}, {"agent reliability"}) < 1.0
    assert novelty_score({"agent", "harness"}, set()) == 1.0
    assert novelty_score({"unrelated"}, {"agent reliability"}) == 1.0


def test_age_bonus():
    old = NOW - timedelta(days=7)
    assert age_bonus(old, NOW, 7) == 1.0
    assert age_bonus(NOW, NOW, 7) == 0.0
    assert age_bonus(NOW - timedelta(days=3), NOW, 7) == 3.0 / 7


def test_rank_pending_sorts_by_signal():
    s1 = CommitSummary(sha="a" * 40, message="fix: handle retry",
                       author_date=datetime(2026, 9, 1, tzinfo=UTC), html_url="u", parents=[])
    s2 = CommitSummary(sha="b" * 40, message="docs: tweak readme",
                       author_date=datetime(2026, 9, 1, tzinfo=UTC), html_url="u", parents=[])
    out = rank_pending([(s2, NOW), (s1, NOW)], set(), DailyBudget(), NOW)
    assert out[0].sha == "a" * 40  # fix 关键词优先于 docs


def test_select_groups_respects_group_cap():
    groups = [[_detail("a" * 40, _files("src/a.py"))] for _ in range(20)]
    budget = DailyBudget(max_change_groups=3)
    selected = select_groups(groups, set(), budget, {}, NOW)
    assert len(selected) == 3


def test_select_groups_prefers_source_over_docs():
    docs = [_detail("d" * 40, _files("docs/x.md"), message="docs: x")]
    src = [_detail("s" * 40, _files("src/a.py"), message="fix: y")]
    selected = select_groups([docs, src], set(), DailyBudget(max_change_groups=1), {}, NOW)
    assert selected == [src]
