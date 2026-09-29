"""社区仓库（文件工作区，原子写；candidates/feedback 为 append-only JSONL）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from finch.communities.models import (
    CommunityContext,
    CommunityFeedback,
    CommunityProfile,
    CommunityRun,
    RunStep,
    identity_key,
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

    def list_latest_profiles(self, week: str | None = None) -> list[CommunityProfile]:
        """每个 identity_key 一条最新投影；给定 week 时先按周过滤再 dedup。"""
        out: dict[str, CommunityProfile] = {}
        for c in self.list_candidates():
            if week is not None and c.week != week:
                continue
            out[identity_key(c)] = c
        return list(out.values())

    def latest_feedback_by_identity(self) -> dict[str, CommunityFeedback]:
        """{identity_key: 最新反馈}，跨 name 漂移（同 canonical_url）也能关联到最新投影。"""
        id_to_key = {c.id: identity_key(c) for c in self.list_candidates()}
        out: dict[str, CommunityFeedback] = {}
        for fb in self.list_feedback():
            out[id_to_key.get(fb.community_id, fb.community_id)] = fb
        return out

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

    def feedback_for(self, community_id: str) -> list[CommunityFeedback]:
        """某个社区的全部反馈（append 顺序），供回访读取。"""
        return [f for f in self.list_feedback() if f.community_id == community_id]

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

    # ---- runs / steps (append-only JSONL) ----
    def append_run(self, run: CommunityRun) -> None:
        self.ws.append_jsonl(self._dir / "runs.jsonl", run.model_dump(mode="json"))

    def list_runs(self) -> list[CommunityRun]:
        """每个 run_id 一条最新投影（append 日志：running → done/failed 只取末态）。"""
        out: dict[str, CommunityRun] = {}
        for row in self.ws.read_jsonl(self._dir / "runs.jsonl"):
            try:
                run = CommunityRun.model_validate(row)
            except ValidationError:
                continue
            out[run.run_id] = run
        return list(out.values())

    def get_run(self, run_id: str) -> CommunityRun | None:
        return next((r for r in reversed(self.list_runs()) if r.run_id == run_id), None)

    def append_step(self, step: RunStep) -> None:
        self.ws.append_jsonl(self._dir / "steps.jsonl", step.model_dump(mode="json"))

    def list_steps(self, run_id: str) -> list[RunStep]:
        out: list[RunStep] = []
        for row in self.ws.read_jsonl(self._dir / "steps.jsonl"):
            try:
                s = RunStep.model_validate(row)
            except ValidationError:
                continue
            if s.run_id == run_id:
                out.append(s)
        return out
