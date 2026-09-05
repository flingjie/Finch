"""每日 Graph 组装（Phase 5 Task F7）：把节点 1–9 串成完整管线。"""

from ..codex.runner import CodexRunner
from ..content.voice import VoiceProfile
from ..content.writer import rewrite, write_original, write_reply
from ..evidence.extractor import Extractor
from ..github.gh_client import GhClient
from ..github.models import CommitDetail
from ..llm.base import StructuredInferenceRunner
from ..settings import Settings
from ..storage.database import Store
from ..storage.repositories import (
    CommitIngestionRepository,
    ContentJobRepository,
    EvidenceRepository,
)
from ..twitter.models import DiscussionCandidate, to_candidate
from ..twitter.normalizer import normalize_tweets
from ..twitter.opencli_client import OpenCliClient
from ..twitter.query_builder import QueryBuilder
from .content_nodes import (
    default_checker_suite,
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
)


def daily_nodes(
    *,
    settings: Settings,
    store: Store,
    gh: GhClient,
    opencli: OpenCliClient,
    extractor: Extractor,
    runner: CodexRunner,
    groups_by_repo: dict[str, list[list[CommitDetail]]],
    known_commit_urls: set[str],
    repo_is_private: dict[str, bool],
    voice_profile: VoiceProfile | None = None,
    inference_runners: dict[str, StructuredInferenceRunner | None] | None = None,
) -> list[Node]:
    """组装每日 Graph：preflight → extract → collect → recall → match → draft →
    critique → brief。提取节点对 groups_by_repo 中的预分组 group 提取事件并合并 Evidence Cards。
    """
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

    jobs_repo = ContentJobRepository(store)

    def _resolve(node_name: str) -> StructuredInferenceRunner:
        if inference_runners is None:
            return runner
        return inference_runners.get(node_name) or runner

    return [
        make_preflight_node(gh, opencli),
        make_extract_node(
            extractor=extractor,
            groups_by_repo=groups_by_repo,
            repo_is_private=repo_is_private,
            known_commit_urls=known_commit_urls,
            cards_repo=EvidenceRepository(store),
            ingestion_repo=CommitIngestionRepository(store),
            max_extract_retries=settings.daily_budget.max_extract_retries,
        ),
        make_collect_node(collect_fn),
        make_recall_node(settings.quality_gates),
        make_match_node(_resolve("match_evidence"), settings.quality_gates, settings.twitter),
        make_define_jobs_node(
            _resolve("plan_topics"),
            _resolve("expand_job"),
            expand_concurrency=settings.llm.for_node("expand_job").max_concurrency,
            jobs_repo=jobs_repo,
        ),
        make_position_gate_node(jobs_repo=jobs_repo),
        make_draft_node(runner, write_reply, write_original, settings.quality_gates),
        make_critique_node(
            runner, rewrite, settings.quality_gates,
            checkers=default_checker_suite(_resolve("critique"), voice_profile),
            voice_profile=voice_profile,
        ),
        make_brief_node(settings.quality_gates, jobs_repo=jobs_repo),
    ]
