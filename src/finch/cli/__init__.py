"""Finch CLI（spec 10）。"""

import sys
import types

import typer

from finch.cli import frozen, problem_discovery, products, relationships, sources

app = typer.Typer(help="Finch: AI 产品发现与创业者连接 Copilot。")

# 冻结（hidden=True）：旧使命子命令从默认 `finch --help` 面移除，但命令仍可调用、
# 代码与测试保留；验证新方向后再决定删除。恢复只需删掉 hidden=True。
app.add_typer(frozen.github_app, name="github", hidden=True)

app.add_typer(sources.twitter_app, name="twitter")

app.add_typer(sources.sources_app, name="sources")

app.add_typer(sources.repos_app, name="repos")

app.add_typer(frozen.voice_app, name="voice", hidden=True)

app.add_typer(frozen.profile_app, name="profile", hidden=True)

app.add_typer(frozen.ideas_app, name="ideas", hidden=True)

app.add_typer(frozen.drafts_app, name="drafts", hidden=True)

app.add_typer(frozen.review_app, name="review", hidden=True)

app.add_typer(frozen.connect_app, name="connect", hidden=True)

app.add_typer(frozen.peers_app, name="peers", hidden=True)

app.add_typer(frozen.people_app, name="people", hidden=True)

app.add_typer(frozen.connections_app, name="connections", hidden=True)

app.add_typer(frozen.collisions_app, name="collisions", hidden=True)

app.add_typer(frozen.experiments_app, name="experiments", hidden=True)

app.add_typer(frozen.inspirations_app, name="inspirations", hidden=True)

app.add_typer(frozen.conversations_app, name="conversations", hidden=True)

app.add_typer(frozen.practice_app, name="practice", hidden=True)

app.add_typer(frozen.problems_app, name="problems", hidden=True)

app.add_typer(frozen.attempts_app, name="attempts", hidden=True)

app.add_typer(frozen.grounded_app, name="grounded", hidden=True)

app.add_typer(frozen.knowledge_app, name="knowledge", hidden=True)

app.add_typer(frozen.angles_app, name="angles", hidden=True)

app.add_typer(frozen.article_app, name="article", hidden=True)

app.add_typer(frozen.methods_app, name="methods", hidden=True)

app.add_typer(frozen.community_app, name="community", hidden=True)

app.add_typer(frozen.dialogue_app, name="dialogue", hidden=True)

app.add_typer(frozen.summaries_app, name="summaries", hidden=True)

app.add_typer(frozen.materials_app, name="materials", hidden=True)

app.add_typer(products.products_app, name="products")

app.add_typer(relationships.relationships_app, name="relationships")

app.add_typer(problem_discovery.problem_discovery_app, name="problem-discovery")


app.command()(sources.init)

app.command(hidden=True)(frozen.context)

app.command()(sources.diagnose)

app.command("learn", hidden=True)(frozen.learn)
app.command("weekly", hidden=True)(frozen.weekly)
app.command("summarize", hidden=True)(frozen.summarize)


# 兼容再导出：现有测试通过 `finch.cli.<name>` 导入/打补丁，保持零改动。
# isort: off
from finch.cli.frozen import (  # noqa: E402,F401
    _lookback_hours,
    _persist_discovery,
    _render_opportunity_assessment_coverage,
    _render_preferred_opportunity,
    _render_preferred_skips,
    _require_notion,
    _run_daily_full,
    _run_discovery,
    conversations_get,
    peers_get,
)
from finch.angle_discovery.mindmap_service import MindMapService  # noqa: E402,F401
from finch.angle_discovery.service import AngleDiscoveryService  # noqa: E402,F401
from finch.article.service import ArticleAnalysisService  # noqa: E402,F401
from finch.content.voice import load_voice_profile  # noqa: E402,F401
from finch.content.writer import rewrite_with_instruction  # noqa: E402,F401
from finch.content_summary.service import ContentSummaryService  # noqa: E402,F401
from finch.drafts.service import DraftService  # noqa: E402,F401
from finch.evidence.extractor import Extractor  # noqa: E402,F401
from finch.github.commit_reader import CommitReader  # noqa: E402,F401
from finch.github.commit_reader import load_commit_details  # noqa: E402,F401
from finch.ideas.commit_service import CommitService  # noqa: E402,F401
from finch.ideas.divergence import IdeaDiverger  # noqa: E402,F401
from finch.ideas.fragment_service import FragmentService  # noqa: E402,F401
from finch.learn.reflection import WeeklyReflectionService  # noqa: E402,F401
from finch.opportunities.prepare import prepare_contribution  # noqa: E402,F401
from finch.storage.repositories import ConversationThreadRepository  # noqa: E402,F401
from finch.codex.runner import CodexRunner  # noqa: E402,F401
from finch.github.gh_client import GhClient  # noqa: E402,F401
from finch.llm.openai_compatible import create_runner  # noqa: E402,F401
from finch.products.discovery import run_product_discovery  # noqa: E402,F401
from finch.settings import load_settings  # noqa: E402,F401
from finch.twitter.opencli_client import OpenCliClient  # noqa: E402,F401
# isort: on


_SUBMODULES = ("frozen", "problem_discovery", "products", "relationships", "sources")


class _CliModule(types.ModuleType):
    """让 `monkeypatch.setattr(finch.cli, name, ...)` 同步到已抽出的子模块。

    命令体原样搬进 `frozen.py` 等子模块后，其模块级名字（如 `load_settings`）不再与本包共享；
    测试仍对 `finch.cli.<name>` 打补丁，这里把补丁转发给定义了同名全局的子模块。
    """

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        if name.startswith("__") or name in _SUBMODULES:
            return
        for mod in (frozen, problem_discovery, products, relationships, sources):
            if name in vars(mod):
                setattr(mod, name, value)


sys.modules[__name__].__class__ = _CliModule


if __name__ == "__main__":
    app()
