"""社区仓库（文件工作区，原子写；candidates/feedback 为 append-only JSONL）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from finch.communities.models import (
    CommunityContext,
    CommunityFeedback,
    CommunityProfile,
)
from finch.storage.workspace import Workspace


class CommunityRepository:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._dir = workspace.dir("communities")

    # ---- context (profile.yaml) ----
    def write_context(self, context: CommunityContext) -> None:
        self.ws.write_yaml(self._dir / "profile.yaml", context)

    def read_context(self) -> CommunityContext | None:
        return self.ws.read_yaml(self._dir / "profile.yaml", CommunityContext)

    # ---- candidates (candidates.jsonl, append-only) ----
    def append_candidate(self, profile: CommunityProfile) -> None:
        self.ws.append_jsonl(
            self._dir / "candidates.jsonl", profile.model_dump(mode="json")
        )

    def list_candidates(self) -> list[CommunityProfile]:
        rows = self.ws.read_jsonl(self._dir / "candidates.jsonl")
        out: list[CommunityProfile] = []
        for row in rows:
            try:
                out.append(CommunityProfile.model_validate(row))
            except ValidationError:
                continue  # 跳过旧 schema / 手工损坏的行，不阻断读取
        return out

    def get_candidate(self, community_id: str) -> CommunityProfile | None:
        # 追加日志：同 id 多条时取最新一条。
        return next(
            (c for c in reversed(self.list_candidates()) if c.id == community_id),
            None,
        )

    # ---- feedback (feedback.jsonl, append-only) ----
    def append_feedback(self, feedback: CommunityFeedback) -> None:
        self.ws.append_jsonl(
            self._dir / "feedback.jsonl", feedback.model_dump(mode="json")
        )

    def list_feedback(self) -> list[CommunityFeedback]:
        rows = self.ws.read_jsonl(self._dir / "feedback.jsonl")
        out: list[CommunityFeedback] = []
        for row in rows:
            try:
                out.append(CommunityFeedback.model_validate(row))
            except ValidationError:
                continue
        return out

    def latest_feedback(self, community_id: str) -> CommunityFeedback | None:
        return next(
            (f for f in reversed(self.list_feedback()) if f.community_id == community_id),
            None,
        )

    def latest_feedback_by_id(self) -> dict[str, CommunityFeedback]:
        """一次读取 feedback.jsonl，返回 {community_id: 最新一条}。"""
        out: dict[str, CommunityFeedback] = {}
        for fb in self.list_feedback():
            out[fb.community_id] = fb
        return out

    # ---- reports (reports/<week>.md) ----
    def report_path(self, week: str) -> Path:
        return self._dir / "reports" / f"{week}.md"

    def write_report(self, week: str, text: str) -> None:
        path = self.report_path(week)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ws.atomic_write(path, text)

    def read_report(self, week: str) -> str | None:
        path = self.report_path(week)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
