"""CreatorEvidence 领域服务：Codex 批量判断 → 校验 → 幂等落仓。

每次最多 ``max_persons`` 人进入语义评估，按 ``batch_size`` 分批（默认 5 人/批），
每批一次 Codex 调用。单批失败只降级该批，不阻断其他批。缓存键含 prompt 版本，
artifact 内容变化（fingerprint）会在 ``assess`` 的 pending 判定中触发重评。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from finch.codex.runner import CodexRunner
from finch.llm.base import StructuredInferenceRunner
from finch.peers.evidence_repo import CreatorEvidenceRepository
from finch.peers.models import PeerProfile
from finch.peers.person import CreatorEvidence, CreatorEvidenceKind, Person
from finch.peers.person_service import PersonRepository
from finch.sources.models import RawArtifact
from finch.sources.store import ArtifactRepository
from finch.storage.repositories import PeerRepository
from finch.storage.workspace import Workspace

_BATCH_PROMPT_PATH = Path("prompts/creator-evidence-batch.md")
_MAX_PERSONS = 20
_MAX_ARTIFACTS = 3
_TEXT_LIMIT = 600
_BATCH_SIZE = 5
_EVIDENCE_PROMPT_VERSION = "creator-evidence-batch-v1"


class CreatorEvidenceItem(BaseModel):
    """LLM 输出项（无 evidence_id / total）。"""

    artifact_id: str
    kind: CreatorEvidenceKind
    claim: str
    support: list[str] = Field(default_factory=list)
    first_hand: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    counter_evidence: list[str] = Field(default_factory=list)


class CreatorEvidenceBatchPerson(BaseModel):
    """批量输出中一个人物的证据项。"""

    person_id: str
    items: list[CreatorEvidenceItem] = Field(default_factory=list)


class CreatorEvidenceBatchOutput(BaseModel):
    """一次批量 Codex 调用返回多个人物的证据。"""

    persons: list[CreatorEvidenceBatchPerson] = Field(default_factory=list)


@dataclass
class EvidenceAssessResult:
    assessed_persons: int = 0
    saved: int = 0
    skipped_persons: int = 0
    failures: list[str] = field(default_factory=list)


def evidence_id_for(
    person_id: str,
    artifact_id: str,
    *,
    version: str = _EVIDENCE_PROMPT_VERSION,
) -> str:
    """稳定 evidence_id：人物 + artifact + prompt 版本。"""
    digest = hashlib.sha256(f"{person_id}:{artifact_id}:{version}".encode()).hexdigest()
    return f"ce_{digest[:12]}"


def _truncate(text: str, n: int = _TEXT_LIMIT) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def _artifact_row(a: RawArtifact) -> dict:
    return {
        "artifact_id": a.artifact_id,
        "source": a.source.value,
        "source_type": a.source_type,
        "url": a.canonical_url,
        "title": a.title or "",
        "text": _truncate(a.text),
    }


def _persons_payload(batch: list[tuple[PeerProfile, Person, list[RawArtifact]]]) -> str:
    rows = [
        {
            "person_id": person.person_id,
            "peer_id": peer.id,
            "display_name": person.display_name or peer.display_name,
            "artifacts": [_artifact_row(a) for a in arts],
        }
        for peer, person, arts in batch
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)


def _chunks(seq: list, n: int) -> list[list]:
    return [seq[i : i + n] for i in range(0, len(seq), n)]


class CreatorEvidenceService:
    """对有未评估 artifact 的 Person 批量调用 Codex，失败则跳过该批。"""

    def __init__(
        self,
        workspace: Workspace,
        *,
        runner: StructuredInferenceRunner | CodexRunner | None = None,
        max_persons: int = _MAX_PERSONS,
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self.ws = workspace
        self.runner = runner
        self.max_persons = max_persons
        self.batch_size = batch_size
        self.peers = PeerRepository(workspace)
        self.people = PersonRepository(workspace)
        self.artifacts = ArtifactRepository(workspace)
        self.evidence = CreatorEvidenceRepository(workspace)

    def assess(
        self,
        *,
        peer_ids: list[str] | None = None,
    ) -> EvidenceAssessResult:
        if self.runner is None:
            return EvidenceAssessResult(failures=["no runner configured"])

        result = EvidenceAssessResult()
        peers = self.peers.list_all()
        if peer_ids is not None:
            wanted = set(peer_ids)
            peers = [p for p in peers if p.id in wanted]

        candidates: list[tuple[PeerProfile, Person, list[RawArtifact]]] = []
        for peer in peers:
            if not peer.person_id:
                continue
            person = self.people.get(peer.person_id)
            if person is None:
                continue
            existing = self.evidence.list_for_person(person.person_id)
            known = {e.artifact_id for e in existing}
            arts = self.artifacts.list_by_ids(peer.source_refs)
            pending = [a for a in arts if a.artifact_id not in known]
            # Need ≥2 total artifacts to be shortlist-eligible; assess when any pending
            if len(arts) < 2 or not pending:
                continue
            take = (pending + [a for a in arts if a.artifact_id in known])[:_MAX_ARTIFACTS]
            candidates.append((peer, person, take))

        for batch in _chunks(candidates[: self.max_persons], self.batch_size):
            try:
                saved = self._assess_batch(batch)
                result.assessed_persons += len(batch)
                result.saved += saved
            except Exception as exc:  # noqa: BLE001
                result.skipped_persons += len(batch)
                result.failures.append(f"batch: {exc}"[:200])
        return result

    def _assess_batch(
        self, batch: list[tuple[PeerProfile, Person, list[RawArtifact]]]
    ) -> int:
        assert self.runner is not None
        allowed_by_person: dict[str, set[str]] = {
            person.person_id: {a.artifact_id for a in arts}
            for _, person, arts in batch
        }
        peer_by_person = {person.person_id: peer for peer, person, _ in batch}
        prompt = _BATCH_PROMPT_PATH.read_text().format(
            persons=_persons_payload(batch),
        )
        output = self.runner.run(prompt, CreatorEvidenceBatchOutput)
        assert isinstance(output, CreatorEvidenceBatchOutput)
        saved = 0
        for person_out in output.persons:
            allowed = allowed_by_person.get(person_out.person_id)
            peer = peer_by_person.get(person_out.person_id)
            if allowed is None or peer is None:
                continue
            for item in person_out.items:
                if item.artifact_id not in allowed:
                    continue
                if not item.claim.strip():
                    continue
                support = [s for s in item.support if s in allowed] or [item.artifact_id]
                ev = CreatorEvidence(
                    evidence_id=evidence_id_for(
                        person_out.person_id, item.artifact_id
                    ),
                    person_id=person_out.person_id,
                    peer_id=peer.id,
                    artifact_id=item.artifact_id,
                    kind=item.kind,
                    claim=item.claim.strip(),
                    support=support,
                    first_hand=item.first_hand,
                    confidence=item.confidence,
                    counter_evidence=list(item.counter_evidence),
                )
                self.evidence.save(ev)
                saved += 1
        return saved
