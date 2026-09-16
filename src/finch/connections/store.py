"""ConnectionOpportunity 持久化。"""

from __future__ import annotations

from pathlib import Path

from finch.connections.service import ConnectionOpportunity
from finch.storage.workspace import Workspace


class ConnectionOpportunityRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("connections")

    def _path(self, opportunity_id: str) -> Path:
        return Path(self._dir) / f"{Workspace.safe_filename(opportunity_id)}.yaml"

    def save(self, opportunity: ConnectionOpportunity) -> None:
        self.ws.write_yaml(self._path(opportunity.opportunity_id), opportunity)

    def get(self, opportunity_id: str) -> ConnectionOpportunity | None:
        return self.ws.read_yaml(self._path(opportunity_id), ConnectionOpportunity)

    def list_all(self) -> list[ConnectionOpportunity]:
        out: list[ConnectionOpportunity] = []
        for path in sorted(Path(self._dir).glob("conn_*.yaml")):
            row = self.ws.read_yaml(path, ConnectionOpportunity)
            if row is not None:
                out.append(row)
        return out

    def list_for_person(self, person_id: str) -> list[ConnectionOpportunity]:
        return [o for o in self.list_all() if o.person_id == person_id]
