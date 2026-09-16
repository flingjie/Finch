"""Person 关联服务：强证据自动 confirmed；同名 alone 禁止自动合并。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from finch.peers.models import PeerProfile, Platform
from finch.peers.person import (
    IdentityConfidence,
    IdentityLinkAudit,
    LinkedIdentity,
    Person,
    person_id_for,
)
from finch.peers.service import peer_id_for
from finch.storage.workspace import Workspace


class PersonRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("people")
        self._audit = workspace.dir("people") / "_identity_audit.jsonl"

    def _path(self, person_id: str) -> object:
        from pathlib import Path

        return Path(self._dir) / f"{Workspace.safe_filename(person_id)}.yaml"

    def get(self, person_id: str) -> Person | None:
        return self.ws.read_yaml(self._path(person_id), Person)  # type: ignore[arg-type]

    def save(self, person: Person) -> None:
        self.ws.write_yaml(self._path(person.person_id), person)  # type: ignore[arg-type]

    def list_all(self) -> list[Person]:
        from pathlib import Path

        out: list[Person] = []
        for path in sorted(Path(self._dir).glob("person_*.yaml")):
            person = self.ws.read_yaml(path, Person)
            if person is not None:
                out.append(person)
        return out

    def append_audit(self, event: IdentityLinkAudit) -> None:
        self.ws.append_jsonl(self._audit, event.model_dump(mode="json"))


class PersonService:
    """Person 链接层；PeerProfile 仍是 (platform, author_id) 原子。"""

    def __init__(self, repo: PersonRepository) -> None:
        self.repo = repo

    def ensure_from_peer(self, peer: PeerProfile) -> Person:
        """若 peer 尚无 person_id，创建单身份 Person 并回写关联。"""
        if peer.person_id:
            existing = self.repo.get(peer.person_id)
            if existing is not None:
                return existing
        identity = peer.platform_identities[0] if peer.platform_identities else None
        if identity is None:
            pid = person_id_for(peer.id)
            person = Person(person_id=pid, display_name=peer.display_name)
            self.repo.save(person)
            return person
        pid = person_id_for(identity.platform, identity.author_id)
        person = Person(
            person_id=pid,
            display_name=peer.display_name,
            identities=[
                LinkedIdentity(
                    platform=identity.platform,
                    external_id=identity.author_id,
                    handle=identity.username or identity.author_id,
                    peer_id=peer.id,
                    confidence=IdentityConfidence.CONFIRMED,
                    url=identity.url,
                )
            ],
        )
        self.repo.save(person)
        return person

    def propose_link(
        self,
        person: Person,
        *,
        platform: Platform,
        external_id: str,
        handle: str = "",
        mutual_profile_link: bool = False,
        weak_signals: int = 0,
        reason: str = "",
    ) -> tuple[Person, IdentityLinkAudit | None]:
        """链接候选：主页互链 → confirmed；同名+多项弱证据 → probable；仅同名 → 拒绝。"""
        peer_id = peer_id_for(platform, external_id)
        for existing in person.identities:
            if existing.platform == platform and existing.external_id == external_id:
                return person, None

        # Same handle / username alone: never auto-merge.
        if not mutual_profile_link and weak_signals < 2:
            audit = IdentityLinkAudit(
                event_id=f"id_{uuid4().hex[:12]}",
                action="reject",
                person_id=person.person_id,
                peer_id=peer_id,
                platform=platform,
                external_id=external_id,
                confidence=IdentityConfidence.PENDING,
                reason=reason or "insufficient evidence; no auto-merge on username alone",
            )
            self.repo.append_audit(audit)
            return person, audit

        if mutual_profile_link:
            confidence = IdentityConfidence.CONFIRMED
        else:
            confidence = IdentityConfidence.PROBABLE

        linked = LinkedIdentity(
            platform=platform,
            external_id=external_id,
            handle=handle or external_id,
            peer_id=peer_id,
            confidence=confidence,
        )
        updated = person.model_copy(deep=True)
        updated.identities.append(linked)
        self.repo.save(updated)
        audit = IdentityLinkAudit(
            event_id=f"id_{uuid4().hex[:12]}",
            action="link",
            person_id=person.person_id,
            peer_id=peer_id,
            platform=platform,
            external_id=external_id,
            confidence=confidence,
            reason=reason,
            at=datetime.now(UTC),
        )
        self.repo.append_audit(audit)
        return updated, audit

    def unlink(
        self, person: Person, *, platform: Platform, external_id: str, reason: str = ""
    ) -> Person:
        updated = person.model_copy(deep=True)
        updated.identities = [
            i
            for i in updated.identities
            if not (i.platform == platform and i.external_id == external_id)
        ]
        self.repo.save(updated)
        self.repo.append_audit(
            IdentityLinkAudit(
                event_id=f"id_{uuid4().hex[:12]}",
                action="unlink",
                person_id=person.person_id,
                peer_id=peer_id_for(platform, external_id),
                platform=platform,
                external_id=external_id,
                confidence=IdentityConfidence.PENDING,
                reason=reason,
            )
        )
        return updated
