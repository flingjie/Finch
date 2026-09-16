"""原始采集落盘与 Artifact 仓库（幂等）。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from finch.sources.fingerprint import idempotency_key
from finch.sources.models import RawArtifact, Source
from finch.storage.workspace import Workspace


class RawStore:
    """``var/raw/{source}/{date}/{run_id}/`` 原始 JSON 落盘。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace

    def begin_run(self, source: Source) -> tuple[str, Path]:
        run_id = f"run_{uuid4().hex[:12]}"
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self.ws.dir(f"raw/{source.value}/{day}/{run_id}")
        return run_id, path

    def write_raw(
        self, run_dir: Path, item_id: str, payload: dict[str, Any] | list[Any]
    ) -> str:
        safe = Workspace.safe_filename(item_id)
        path = run_dir / f"item_{safe}.json"
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        self.ws.atomic_write(path, text)
        try:
            return str(path.relative_to(self.ws.root))
        except ValueError:
            return str(path)


class ArtifactRepository:
    """``var/artifacts/{artifact_id}.yaml``；按幂等键去重。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("artifacts")
        self._index_path = workspace.dir("sources") / "artifact_index.jsonl"

    def _path(self, artifact_id: str) -> Path:
        return self._dir / f"{Workspace.safe_filename(artifact_id)}.yaml"

    def get(self, artifact_id: str) -> RawArtifact | None:
        return self.ws.read_yaml(self._path(artifact_id), RawArtifact)

    def upsert(self, artifact: RawArtifact) -> tuple[RawArtifact, bool]:
        """写入 Artifact；若相同幂等键已存在则跳过。返回 (artifact, created)。"""
        fp = artifact.content_fingerprint
        key = idempotency_key(
            artifact.source.value,
            artifact.source_type,
            artifact.source_id,
            fp,
        )
        existing = self.get(artifact.artifact_id)
        if existing is not None and existing.content_fingerprint == fp:
            return existing, False
        self.ws.write_yaml(self._path(artifact.artifact_id), artifact)
        self.ws.append_jsonl(
            self._index_path,
            {
                "event_id": f"art_{uuid4().hex[:12]}",
                "idempotency_key": key,
                "artifact_id": artifact.artifact_id,
                "at": datetime.now(UTC).isoformat(),
            },
        )
        return artifact, True

    def list_all(self) -> list[RawArtifact]:
        out: list[RawArtifact] = []
        for path in sorted(self._dir.glob("*.yaml")):
            art = self.ws.read_yaml(path, RawArtifact)
            if art is not None:
                out.append(art)
        return out

    def list_by_ids(self, artifact_ids: list[str]) -> list[RawArtifact]:
        out: list[RawArtifact] = []
        for aid in artifact_ids:
            art = self.get(aid)
            if art is not None:
                out.append(art)
        return out


class CursorStore:
    """``var/sources/cursors/{source}.yaml``。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("sources/cursors")

    def get(self, source: Source) -> str | None:
        path = self._dir / f"{source.value}.yaml"
        if not path.exists():
            return None
        data = path.read_text(encoding="utf-8")
        import yaml

        parsed = yaml.safe_load(data) or {}
        return parsed.get("cursor")

    def set(self, source: Source, cursor: str) -> None:
        import yaml

        path = self._dir / f"{source.value}.yaml"
        self.ws.atomic_write(
            path,
            yaml.safe_dump(
                {"source": source.value, "cursor": cursor},
                sort_keys=False,
                allow_unicode=True,
            ),
        )


class RunRecorder:
    """``var/sources/runs/{run_id}.yaml``。"""

    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("sources/runs")

    def save(self, run_id: str, payload: dict[str, Any]) -> None:
        import yaml

        path = self._dir / f"{Workspace.safe_filename(run_id)}.yaml"
        self.ws.atomic_write(
            path,
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        )
