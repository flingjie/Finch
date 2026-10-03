"""ArticleReport Workspace persistence."""

from finch.article.models import (
    ArticleReport,
    AudienceChange,
    Effectiveness,
    ExpressionTask,
    TransferableMethod,
)
from finch.article.repository import ArticleReportRepository
from finch.storage.workspace import Workspace


def _report(**kw) -> ArticleReport:
    base = dict(
        id="article_abc",
        source_type="text",
        source_ref=None,
        content_hash="hash1",
        expression_task=ExpressionTask(topic="t", primary_task="解释"),
        audience_change=AudienceChange(
            who="dev", before="a", after="b", fit_check="ok"
        ),
        effectiveness=Effectiveness(
            clarity="c", concreteness="c", credibility="c", actionability="n/a"
        ),
        transferable_methods=[
            TransferableMethod(
                method="m1",
                why_effective_here="w",
                when_to_use="u",
                mini_exercise="e",
            ),
            TransferableMethod(
                method="m2",
                why_effective_here="w",
                when_to_use="u",
                mini_exercise="e",
            ),
        ],
    )
    base.update(kw)
    return ArticleReport(**base)


def test_upsert_and_get(tmp_path):
    repo = ArticleReportRepository(Workspace(tmp_path))
    repo.upsert(_report())
    got = repo.get("article_abc")
    assert got is not None
    assert got.content_hash == "hash1"
    assert len(got.transferable_methods) == 2


def test_upsert_overwrites_same_id(tmp_path):
    repo = ArticleReportRepository(Workspace(tmp_path))
    repo.upsert(_report(content_hash="h1"))
    repo.upsert(_report(content_hash="h2"))
    assert repo.get("article_abc").content_hash == "h2"
