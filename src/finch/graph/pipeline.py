"""每日 Graph 节点 1–4：preflight / sync / extract / collect（Phase 4 Task B4）。"""

from collections.abc import Callable
from typing import cast

from pydantic import BaseModel

from ..evidence.extractor import Extractor, build_cards
from ..evidence.safety import scan_cards
from ..github.gh_client import GhClient
from ..github.models import CommitDetail
from ..storage.repositories import EvidenceRepository
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


def make_sync_node(sync_fn: Callable[[], None]) -> Node:
    class SyncNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            sync_fn()
            return NodeResult(status="succeeded")

    return SyncNode(name="sync_commits", writes="", succeeds_to="COMMITS_SYNCED")


def make_extract_node(
    *,
    repo: str,
    extractor: Extractor,
    commits: list[CommitDetail],
    repo_is_private: dict[str, bool],
    known_commit_urls: set[str],
    cards_repo: EvidenceRepository,
) -> Node:
    class ExtractNode(Node):
        def run(self, ctx: dict) -> NodeResult:
            events = extractor.extract(commits, repo)
            cards = build_cards(events)
            if repo_is_private.get(repo, False):
                cards = [c.model_copy(update={"publishable": False}) for c in cards]
            report = scan_cards(
                cards,
                repo_is_private=repo_is_private,
                known_commit_urls=known_commit_urls,
            )
            if report.hard_fail:
                return NodeResult(
                    status="failed", error_code=report.hits[0].code, retryable=False
                )
            for card in cards:
                cards_repo.upsert_card(card)
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
