"""ArtifactProjector：RawArtifact → PeerProfile + Person（幂等）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from finch.peers.person_service import PersonRepository, PersonService
from finch.peers.service import PeerService
from finch.sources.models import RawArtifact
from finch.storage.repositories import PeerRepository
from finch.storage.workspace import Workspace

_VALID_PLATFORMS: set[str] = {"x", "reddit", "github", "v2ex", "weixin", "xiaohongshu"}


@dataclass
class ProjectResult:
    """一轮投影结果。"""

    projected_peers: int = 0
    created_peers: int = 0
    updated_peers: int = 0
    skipped: int = 0
    peer_ids: list[str] = field(default_factory=list)


def _author_key(art: RawArtifact) -> tuple[str, str] | None:
    ident = art.author_identity
    platform = (ident.platform or "").strip()
    author_id = (ident.external_id or ident.handle or "").strip()
    if not platform or not author_id:
        return None
    if platform not in _VALID_PLATFORMS:
        return None
    return platform, author_id


def _truncate(text: str, n: int = 200) -> str:
    text = text.strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


class ArtifactProjector:
    """把本轮 RawArtifact 投影为 Peer + Person，并回写 person_id / source_refs。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self.peers = PeerRepository(workspace)
        self.peer_svc = PeerService()
        self.person_svc = PersonService(PersonRepository(workspace))

    def project(self, artifacts: list[RawArtifact]) -> ProjectResult:
        # Group by (platform, author_id)
        groups: dict[tuple[str, str], list[RawArtifact]] = {}
        result = ProjectResult()
        for art in artifacts:
            key = _author_key(art)
            if key is None:
                result.skipped += 1
                continue
            groups.setdefault(key, []).append(art)

        for (platform, author_id), arts in groups.items():
            handle = ""
            url = None
            for a in arts:
                if a.author_identity.handle:
                    handle = a.author_identity.handle
                if a.canonical_url and not url:
                    # Prefer profile-ish urls later; keep first artifact url as fallback none
                    pass
            discovered = self.peer_svc.from_author(
                platform=platform,  # type: ignore[arg-type]
                author_id=author_id,
                username=handle or author_id,
            )
            existing = self.peers.get(discovered.id)
            was_new = existing is None
            peer = self.peer_svc.merge_discovered(existing, discovered)

            # Append artifact_ids to source_refs (dedupe, preserve order)
            refs = list(peer.source_refs)
            seen = set(refs)
            for a in arts:
                if a.artifact_id not in seen:
                    refs.append(a.artifact_id)
                    seen.add(a.artifact_id)

            # current_work from newest title/text
            latest = max(arts, key=lambda a: a.published_at or a.retrieved_at)
            work = _truncate(latest.title or latest.text or "")
            display = peer.display_name or handle or author_id
            if handle and (not peer.display_name or peer.display_name == author_id):
                display = handle

            peer = peer.model_copy(
                update={
                    "source_refs": refs,
                    "display_name": display,
                    "current_work": work or peer.current_work,
                }
            )

            # Ensure Person and backfill person_id
            person = self.person_svc.ensure_from_peer(peer)
            now = datetime.now(UTC)
            person = person.model_copy(update={"last_evidence_at": now})
            if display and not person.display_name:
                person = person.model_copy(update={"display_name": display})
            self.person_svc.repo.save(person)

            if peer.person_id != person.person_id:
                peer = peer.model_copy(update={"person_id": person.person_id})

            self.peers.upsert(peer)
            result.projected_peers += 1
            result.peer_ids.append(peer.id)
            if was_new:
                result.created_peers += 1
            else:
                result.updated_peers += 1

        return result
