"""CreatorEvidence 仓库（与用户 GitHub EvidenceCard 分离）。"""

from __future__ import annotations

from pathlib import Path

from finch.peers.person import CreatorEvidence
from finch.storage.workspace import Workspace


class CreatorEvidenceRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("creator_evidence")

    def _path(self, evidence_id: str) -> Path:
        return Path(self._dir) / f"{Workspace.safe_filename(evidence_id)}.yaml"

    def save(self, evidence: CreatorEvidence) -> None:
        self.ws.write_yaml(self._path(evidence.evidence_id), evidence)

    def get(self, evidence_id: str) -> CreatorEvidence | None:
        return self.ws.read_yaml(self._path(evidence_id), CreatorEvidence)

    def list_for_person(self, person_id: str) -> list[CreatorEvidence]:
        out: list[CreatorEvidence] = []
        for path in sorted(Path(self._dir).glob("*.yaml")):
            ev = self.ws.read_yaml(path, CreatorEvidence)
            if ev is not None and ev.person_id == person_id:
                out.append(ev)
        return out
