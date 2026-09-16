"""跨领域碰撞生成：Codex → 校验 → CollisionRepository.save。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.collisions.models import CollisionCard, build_collision
from finch.collisions.repository import CollisionRepository
from finch.llm.base import StructuredInferenceRunner
from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.person import CreatorEvidenceKind
from finch.peers.shortlist import ShortlistItem
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace

_PROMPT_PATH = Path("prompts/collision-lab.md")
_TEXT_LIMIT = 600


class CollisionDraft(BaseModel):
    their_domain: str = ""
    your_domain: str = ""
    surface_similarity: str = ""
    structural_similarity: str = ""
    shared_question: str = ""
    transferable_mechanism: str = ""
    falsifiable_hypothesis: str = ""
    artifact_ids: list[str] = Field(default_factory=list)


@dataclass
class CollisionGenerateResult:
    saved: CollisionCard | None = None
    skipped_reason: str = ""
    failures: list[str] = field(default_factory=list)


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


class CollisionService:
    def __init__(
        self,
        workspace: Workspace,
        *,
        runner: StructuredInferenceRunner | CodexRunner | None = None,
    ) -> None:
        self.ws = workspace
        self.runner = runner
        self.repo = CollisionRepository(workspace)
        self.artifacts = ArtifactRepository(workspace)
        self.evidence = CreatorEvidenceRepository(workspace)

    def generate_from_shortlist(
        self,
        items: list[ShortlistItem],
        *,
        your_domains: list[str],
        prefer_cross_domain: bool = True,
    ) -> CollisionGenerateResult:
        """每周最多生成 1 张；优先有 cross_domain_bridge 证据的人选。"""
        if self.runner is None:
            return CollisionGenerateResult(skipped_reason="no runner")
        if not items:
            return CollisionGenerateResult(skipped_reason="empty shortlist")

        # Prefer candidates with cross-domain evidence
        ordered = list(items)
        if prefer_cross_domain:
            def _cross_score(item: ShortlistItem) -> int:
                evs = self.evidence.list_for_person(item.candidate.person_id)
                return sum(1 for e in evs if e.kind == CreatorEvidenceKind.CROSS_DOMAIN_BRIDGE)

            ordered.sort(key=_cross_score, reverse=True)

        target = ordered[0]
        arts = self.artifacts.list_by_ids(target.candidate.artifact_ids)
        if len(arts) < 2:
            return CollisionGenerateResult(skipped_reason="need ≥2 artifacts")

        allowed = {a.artifact_id for a in arts}
        prompt = _PROMPT_PATH.read_text().format(
            your_domains=", ".join(your_domains) or "(none)",
            person_id=target.candidate.person_id,
            peer_id=target.candidate.peer.id,
            display_name=target.candidate.peer.display_name,
            their_domain_hint=target.candidate.platform or target.candidate.why,
            artifacts=json.dumps(
                [
                    {
                        "artifact_id": a.artifact_id,
                        "title": a.title or "",
                        "text": _truncate(a.text),
                        "url": a.canonical_url,
                    }
                    for a in arts
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        try:
            draft = self.runner.run(prompt, CollisionDraft)
            assert isinstance(draft, CollisionDraft)
        except Exception as exc:  # noqa: BLE001
            return CollisionGenerateResult(failures=[str(exc)[:200]])

        artifact_ids = [a for a in draft.artifact_ids if a in allowed]
        if len(artifact_ids) < 2:
            artifact_ids = list(allowed)[:2]
        card = build_collision(
            their_domain=draft.their_domain,
            your_domain=draft.your_domain or (your_domains[0] if your_domains else "practice"),
            surface_similarity=draft.surface_similarity,
            structural_similarity=draft.structural_similarity,
            shared_question=draft.shared_question,
            transferable_mechanism=draft.transferable_mechanism,
            falsifiable_hypothesis=draft.falsifiable_hypothesis,
            artifact_ids=artifact_ids,
            person_id=target.candidate.person_id,
            peer_id=target.candidate.peer.id,
        )
        if card is None:
            return CollisionGenerateResult(skipped_reason="failed validation")
        self.repo.save(card)
        return CollisionGenerateResult(saved=card)
