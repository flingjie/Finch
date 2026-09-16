"""碰撞与实验仓库。"""

from __future__ import annotations

from pathlib import Path

from finch.collisions.models import CollisionCard, MicroExperiment
from finch.storage.workspace import Workspace


class CollisionRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("collisions")

    def save(self, card: CollisionCard) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(card.collision_id)}.yaml"
        self.ws.write_yaml(path, card)

    def get(self, collision_id: str) -> CollisionCard | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(collision_id)}.yaml"
        return self.ws.read_yaml(path, CollisionCard)

    def list_all(self) -> list[CollisionCard]:
        out: list[CollisionCard] = []
        for path in sorted(Path(self._dir).glob("col_*.yaml")):
            card = self.ws.read_yaml(path, CollisionCard)
            if card is not None:
                out.append(card)
        return out


class ExperimentRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("experiments")

    def save(self, experiment: MicroExperiment) -> None:
        path = Path(self._dir) / f"{Workspace.safe_filename(experiment.experiment_id)}.yaml"
        self.ws.write_yaml(path, experiment)

    def get(self, experiment_id: str) -> MicroExperiment | None:
        path = Path(self._dir) / f"{Workspace.safe_filename(experiment_id)}.yaml"
        return self.ws.read_yaml(path, MicroExperiment)

    def list_all(self) -> list[MicroExperiment]:
        out: list[MicroExperiment] = []
        for path in sorted(Path(self._dir).glob("exp_*.yaml")):
            exp = self.ws.read_yaml(path, MicroExperiment)
            if exp is not None:
                out.append(exp)
        return out
