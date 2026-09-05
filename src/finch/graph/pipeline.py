"""每日 Graph 节点 1–4：preflight / sync / extract / collect（Phase 4 Task B4）。"""

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel

from ..evidence.extractor import Extractor, build_cards
from ..evidence.models import EvidenceCard
from ..evidence.safety import scan_cards
from ..github.gh_client import GhClient
from ..github.models import CommitDetail
from ..storage.repositories import CommitIngestionRepository, EvidenceRepository
from ..twitter.models import DiscussionCandidate, TwitterSourceUnavailable
from ..twitter.opencli_client import OpenCliClient
from .context import items_payload
from .events import NodeResult
from .nodes import Node


def make_preflight_node(gh: GhClient, opencli: OpenCliClient) -> Node:
    class PreflightNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            version = gh.version()
            auth_ok = gh.auth_status().get("ok", False)
            doctor_ok = opencli.doctor().get("ok", False)
            if not version or not auth_ok or not doctor_ok:
                return NodeResult(status="failed", error_code="BLOCKED", retryable=False)
            return NodeResult(status="succeeded")

    return PreflightNode(name="preflight", writes="", succeeds_to="PREFLIGHT_PASSED")


def make_extract_node(
    *,
    extractor: Extractor,
    groups_by_repo: dict[str, list[list[CommitDetail]]],
    repo_is_private: dict[str, bool],
    known_commit_urls: set[str],
    cards_repo: EvidenceRepository,
    ingestion_repo: CommitIngestionRepository,
    max_extract_retries: int,
) -> Node:
    class ExtractNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            cards: list[EvidenceCard] = []
            all_shas: dict[str, list[str]] = {}
            for repo, groups in groups_by_repo.items():
                shas = [c.sha for g in groups for c in g]
                all_shas[repo] = shas
                try:
                    events = extractor.extract_grouped(groups, repo)
                except Exception:  # noqa: BLE001
                    for r, s in all_shas.items():
                        ingestion_repo.mark_failed(r, s, max_extract_retries)
                    raise
                repo_cards = build_cards(events)
                if repo_is_private.get(repo, False):
                    repo_cards = [c.model_copy(update={"publishable": False}) for c in repo_cards]
                cards.extend(repo_cards)

            report = scan_cards(
                cards,
                repo_is_private=repo_is_private,
                known_commit_urls=known_commit_urls,
            )
            if report.hard_fail:
                for r, s in all_shas.items():
                    ingestion_repo.mark_failed(r, s, max_extract_retries)
                return NodeResult(
                    status="failed", error_code=report.hits[0].code, retryable=False
                )
            cards_repo.upsert_cards(cards)
            for r, s in all_shas.items():
                ingestion_repo.mark_extracted(r, s)
            return NodeResult(
                status="succeeded", output=items_payload(cast(list[BaseModel], cards))
            )

    return ExtractNode(
        name="extract_events", reads=[], writes="evidence_cards", succeeds_to="EVENTS_EXTRACTED"
    )


def make_collect_node(collect_fn: Callable[[], list[DiscussionCandidate]]) -> Node:
    class CollectNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            try:
                candidates = collect_fn()
            except TwitterSourceUnavailable:
                return NodeResult(status="failed", error_code="BLOCKED", retryable=False)
            return NodeResult(
                status="succeeded", output=items_payload(cast(list[BaseModel], candidates))
            )

    return CollectNode(
        name="collect_tweets", reads=[], writes="candidates", succeeds_to="TWEETS_COLLECTED"
    )
