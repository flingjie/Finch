"""手工导入公开讨论（GitHub repo / URL / 文本）→ 可引用的 source_ref 列表。

GitHub 走 ``GitHubConnector.fetch_repo_feedback``（issue/PR/discussion → RawArtifact），
手工 URL/文本走 ``ImportedDiscussion``。P0 只做「记录原始材料」，不扩展抓取平台。
"""

from __future__ import annotations

from finch.problem_discovery.ids import discussion_id_for
from finch.problem_discovery.models import ImportedDiscussion
from finch.problem_discovery.repository import DiscussionRepository
from finch.sources.connectors.github import GitHubConnector
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


def import_discussion(
    ws: Workspace,
    *,
    url: str = "",
    repo: str = "",
    text: str = "",
    title: str = "",
    author: str = "",
    connector: GitHubConnector | None = None,
) -> list[str]:
    """导入一条公开讨论，返回 source_ref（artifact_id 或 discussion_id）列表。"""
    if repo:
        conn = connector or GitHubConnector()
        artifacts = conn.fetch_repo_feedback(repo)
        artifact_repo = ArtifactRepository(ws)
        refs: list[str] = []
        for art in artifacts:
            artifact_repo.upsert(art)
            refs.append(art.artifact_id)
        return refs

    discussion = ImportedDiscussion(
        discussion_id=discussion_id_for(url, text),
        kind="url" if url else "text",
        url=url.strip(),
        title=title.strip(),
        text=text.strip(),
        author=author.strip(),
    )
    DiscussionRepository(ws).upsert(discussion)
    return [discussion.discussion_id]
