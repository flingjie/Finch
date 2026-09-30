"""机会聚合仓储：``var/opportunities/<id>/opportunity.yaml`` + ``events.jsonl`` + ``artifacts/``。

快照用 ``atomic_write`` 单文件原子替换；事件用 ``append_jsonl`` 追加。单用户本地、
无文件锁，乐观并发由服务层的 ``expected_revision`` 校验承担。
"""

from pathlib import Path

from finch.opportunities.models import Artifact, Opportunity, OpportunityEvent
from finch.storage.workspace import Workspace


class OpportunityRepository:
    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _dir(self, opportunity_id: str) -> Path:
        return self.ws.dir("opportunities") / self.ws.safe_filename(opportunity_id)

    def save(self, opportunity: Opportunity) -> None:
        self.ws.write_yaml(self._dir(opportunity.id) / "opportunity.yaml", opportunity)

    def get(self, opportunity_id: str) -> Opportunity | None:
        return self.ws.read_yaml(
            self._dir(opportunity_id) / "opportunity.yaml", Opportunity
        )

    def append_event(self, event: OpportunityEvent) -> None:
        self.ws.append_jsonl(
            self._dir(event.opportunity_id) / "events.jsonl", event.model_dump(mode="json")
        )

    def list_events(self, opportunity_id: str) -> list[OpportunityEvent]:
        rows = self.ws.read_jsonl(self._dir(opportunity_id) / "events.jsonl")
        return [OpportunityEvent.model_validate(row) for row in rows]

    def list_all(self) -> list[Opportunity]:
        out: list[Opportunity] = []
        for path in sorted(self.ws.dir("opportunities").glob("*/opportunity.yaml")):
            obj = self.ws.read_yaml(path, Opportunity)
            if obj is not None:
                out.append(obj)
        return out


class ArtifactRepository:
    """成果对象仓储：``var/opportunities/<id>/artifacts/<artifact_id>.yaml``。"""

    def __init__(self, ws: Workspace) -> None:
        self.ws = ws

    def _dir(self, opportunity_id: str) -> Path:
        return (
            self.ws.dir("opportunities")
            / self.ws.safe_filename(opportunity_id)
            / "artifacts"
        )

    def save(self, artifact: Artifact) -> None:
        if artifact.opportunity_id is None:
            raise ValueError(f"artifact {artifact.id} has no opportunity_id")
        self.ws.write_yaml(
            self._dir(artifact.opportunity_id) / f"{self.ws.safe_filename(artifact.id)}.yaml",
            artifact,
        )

    def get(self, opportunity_id: str, artifact_id: str) -> Artifact | None:
        return self.ws.read_yaml(
            self._dir(opportunity_id) / f"{self.ws.safe_filename(artifact_id)}.yaml",
            Artifact,
        )

    def list_by_opportunity(self, opportunity_id: str) -> list[Artifact]:
        d = self._dir(opportunity_id)
        if not d.exists():
            return []
        out: list[Artifact] = []
        for path in sorted(d.glob("*.yaml")):
            obj = self.ws.read_yaml(path, Artifact)
            if obj is not None:
                out.append(obj)
        return out
