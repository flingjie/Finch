"""每日 Graph 组装（Phase 5 Task F7）：把节点 1–9 串成完整管线。"""

from ..codex.runner import CodexRunner
from ..content.critic import critique
from ..content.writer import rewrite, write_original, write_reply
from ..evidence.extractor import Extractor
from ..github.commit_reader import CommitReader
from ..github.gh_client import GhClient
from ..github.models import CommitDetail
from ..settings import Settings
from ..storage.database import Store
from ..storage.repositories import ContentJobRepository, EvidenceRepository
from ..twitter.models import DiscussionCandidate, to_candidate
from ..twitter.normalizer import normalize_tweets
from ..twitter.opencli_client import OpenCliClient
from ..twitter.query_builder import QueryBuilder
from .content_nodes import (
    make_brief_node,
    make_critique_node,
    make_define_jobs_node,
    make_draft_node,
    make_position_gate_node,
)
from .match_nodes import make_match_node, make_recall_node
from .nodes import Node
from .pipeline import (
    make_collect_node,
    make_extract_node,
    make_preflight_node,
    make_sync_node,
)


def daily_nodes(
    *,
    settings: Settings,
    store: Store,
    gh: GhClient,
    opencli: OpenCliClient,
    extractor: Extractor,
    runner: CodexRunner,
    commits_by_repo: dict[str, list[CommitDetail]],
    known_commit_urls: set[str],
    repo_is_private: dict[str, bool],
) -> list[Node]:
    """组装每日 Graph：preflight → sync → extract → collect → recall → match → draft →
    critique → brief。

    MVP 单仓：extract 只对 settings.repositories[0] 用注入的 commits_by_repo。
    """

    def sync_fn() -> None:
        for repo in settings.repositories:
            CommitReader(gh, repo).sync()

    def collect_fn() -> list[DiscussionCandidate]:
        builder = QueryBuilder(
            settings.twitter.queries, per_query_limit=settings.twitter.per_query_limit
        )
        candidates: list[DiscussionCandidate] = []
        for cfg in builder:
            if len(candidates) >= settings.twitter.daily_limit:
                break
            tweets = opencli.search(
                cfg.text, product=cfg.filter, limit=settings.twitter.per_query_limit
            )
            for tweet in normalize_tweets(tweets):
                if len(candidates) >= settings.twitter.daily_limit:
                    break
                candidates.append(to_candidate(tweet, query_id=cfg.id))
        return candidates

    repo = settings.repositories[0]
    jobs_repo = ContentJobRepository(store)

    return [
        make_preflight_node(gh, opencli),
        make_sync_node(sync_fn),
        make_extract_node(
            repo=repo,
            extractor=extractor,
            commits=commits_by_repo[repo],
            repo_is_private=repo_is_private,
            known_commit_urls=known_commit_urls,
            cards_repo=EvidenceRepository(store),
        ),
        make_collect_node(collect_fn),
        make_recall_node(settings.quality_gates),
        make_match_node(runner, settings.quality_gates, settings.twitter),
        make_define_jobs_node(runner, jobs_repo=jobs_repo),
        make_position_gate_node(jobs_repo=jobs_repo),
        make_draft_node(runner, write_reply, write_original, settings.quality_gates),
        make_critique_node(runner, rewrite, critique, settings.quality_gates),
        make_brief_node(settings.quality_gates, jobs_repo=jobs_repo),
    ]
