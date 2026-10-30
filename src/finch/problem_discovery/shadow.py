"""Skill-first 影子实验：candidates 导出、validate-upsert、journal、compare。

宿主 Agent 负责研究推理；本模块只做确定性工具面，不调用 ``assess_problems``。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from finch.problem_discovery.discover import (
    PROBLEM_VERSION,
    ProblemDraft,
    ProblemsExtraction,
    _collect_sources,
    _fingerprint,
    _fingerprint_payload,
    build_problem,
)
from finch.problem_discovery.models import DiscoveryRun, Problem
from finch.problem_discovery.repository import (
    DiscoveryRunRepository,
    DiscussionRepository,
    ProblemRepository,
)
from finch.sources.store import ArtifactRepository
from finch.storage.workspace import Workspace


class CandidatesExport(BaseModel):
    """与 control ``discover_problems`` 同源的候选池快照。"""

    fingerprint: str = ""
    version: str = PROBLEM_VERSION
    budget_stats: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, str]] = Field(default_factory=list)


class ValidateUpsertResult(BaseModel):
    """草稿门禁结果：存活问题 + 丢弃原因。"""

    problems: list[Problem] = Field(default_factory=list)
    dropped: list[dict[str, str]] = Field(default_factory=list)
    draft_count: int = 0
    kept_count: int = 0
    run_id: str = ""
    fingerprint: str = ""


class JournalEntry(BaseModel):
    """影子/对照运行事件（追加写入 JSONL）。"""

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: str = ""  # candidates | sync | validate | note | compare
    shadow: bool = True
    detail: dict[str, Any] = Field(default_factory=dict)


class CompareResult(BaseModel):
    """control vs shadow 最新运行差异。"""

    control_run_id: str = ""
    shadow_run_id: str = ""
    control_fingerprint: str = ""
    shadow_fingerprint: str = ""
    fingerprint_match: bool = False
    control_problem_ids: list[str] = Field(default_factory=list)
    shadow_problem_ids: list[str] = Field(default_factory=list)
    overlap_ids: list[str] = Field(default_factory=list)
    only_control: list[str] = Field(default_factory=list)
    only_shadow: list[str] = Field(default_factory=list)
    control_evidence_count: int = 0
    shadow_evidence_count: int = 0
    notes: list[str] = Field(default_factory=list)


def export_candidates(
    *,
    artifact_repo: ArtifactRepository,
    discussion_repo: DiscussionRepository,
    version: str = PROBLEM_VERSION,
    lookback_hours: int | None = 720,
    max_sources: int | None = 40,
    max_chars_per_source: int | None = 4000,
    now: datetime | None = None,
) -> CandidatesExport:
    """导出与 control 相同裁剪规则下的来源池。"""
    clock = now or datetime.now(UTC)
    source_texts, urls = _collect_sources(
        artifact_repo,
        discussion_repo,
        lookback_hours=lookback_hours,
        max_sources=max_sources,
        max_chars_per_source=max_chars_per_source,
        now=clock,
    )
    payload = _fingerprint_payload(source_texts, version)
    fingerprint = _fingerprint(payload) if source_texts else ""
    sources = [
        {"id": ref, "url": urls.get(ref, ""), "text": text}
        for ref, text in sorted(source_texts.items())
    ]
    return CandidatesExport(
        fingerprint=fingerprint,
        version=version,
        budget_stats={
            "lookback_hours": lookback_hours,
            "max_sources": max_sources,
            "max_chars_per_source": max_chars_per_source,
            "source_count": len(sources),
            "total_chars": sum(len(s["text"]) for s in sources),
        },
        sources=sources,
    )


def _parse_drafts(raw: str | dict[str, Any] | ProblemsExtraction) -> ProblemsExtraction:
    if isinstance(raw, ProblemsExtraction):
        return raw
    if isinstance(raw, dict):
        return ProblemsExtraction.model_validate(raw)
    text = raw.strip()
    if not text:
        return ProblemsExtraction(problems=[])
    data = json.loads(text)
    if isinstance(data, list):
        return ProblemsExtraction(
            problems=[ProblemDraft.model_validate(item) for item in data]
        )
    return ProblemsExtraction.model_validate(data)


def validate_upsert(
    drafts: str | dict[str, Any] | ProblemsExtraction,
    *,
    artifact_repo: ArtifactRepository,
    discussion_repo: DiscussionRepository,
    problem_repo: ProblemRepository,
    run_repo: DiscoveryRunRepository,
    version: str = PROBLEM_VERSION,
    lookback_hours: int | None = 720,
    max_sources: int | None = 40,
    max_chars_per_source: int | None = 4000,
    now: datetime | None = None,
    write_run: bool = True,
) -> ValidateUpsertResult:
    """对宿主草稿跑 ``build_problem`` 门禁并可选写入（含 shadow repo）。"""
    clock = now or datetime.now(UTC)
    extraction = _parse_drafts(drafts)
    if extraction.eval_failed:
        return ValidateUpsertResult(
            dropped=[{"reason": extraction.skip_reason or "eval_failed"}],
            draft_count=0,
        )

    source_texts, urls = _collect_sources(
        artifact_repo,
        discussion_repo,
        lookback_hours=lookback_hours,
        max_sources=max_sources,
        max_chars_per_source=max_chars_per_source,
        now=clock,
    )
    fingerprint = (
        _fingerprint(_fingerprint_payload(source_texts, version)) if source_texts else ""
    )

    kept: list[Problem] = []
    dropped: list[dict[str, str]] = []
    for draft in extraction.problems:
        title = draft.title.strip() or "(untitled)"
        before_ev = len(draft.evidence)
        problem = build_problem(draft, source_texts=source_texts, urls=urls, now=clock)
        if problem is None:
            dropped.append({"title": title, "reason": "empty_title_or_invalid"})
            continue
        dropped_ev = before_ev - len(problem.evidence)
        if dropped_ev > 0:
            dropped.append(
                {
                    "title": title,
                    "reason": f"evidence_dropped:{dropped_ev}",
                    "problem_id": problem.problem_id,
                }
            )
        problem_repo.upsert(problem)
        kept.append(problem)

    run_id = ""
    if write_run and kept:
        run_id = f"run_{clock:%Y%m%d%H%M%S}"
        run_repo.upsert(
            DiscoveryRun(
                run_id=run_id,
                created_at=clock,
                fingerprint=fingerprint,
                problem_ids=[p.problem_id for p in kept],
            )
        )
    return ValidateUpsertResult(
        problems=kept,
        dropped=dropped,
        draft_count=len(extraction.problems),
        kept_count=len(kept),
        run_id=run_id,
        fingerprint=fingerprint,
    )


def _journal_path(ws: Workspace, *, shadow: bool) -> Path:
    root = "problem_discovery/shadow/journal" if shadow else "problem_discovery/journal"
    return ws.dir(root) / "events.jsonl"


def journal_append(
    ws: Workspace,
    entry: JournalEntry,
    *,
    shadow: bool = True,
) -> None:
    path = _journal_path(ws, shadow=shadow)
    line = json.dumps(entry.model_dump(mode="json"), ensure_ascii=False)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    ws.atomic_write(path, existing + line + "\n")


def journal_show(ws: Workspace, *, shadow: bool = True, limit: int = 50) -> list[JournalEntry]:
    path = _journal_path(ws, shadow=shadow)
    if not path.exists():
        return []
    rows: list[JournalEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(JournalEntry.model_validate(json.loads(line)))
        except Exception:  # noqa: BLE001
            continue
    return rows[-limit:]


def compare_runs(
    *,
    control_run_repo: DiscoveryRunRepository,
    shadow_run_repo: DiscoveryRunRepository,
    control_problem_repo: ProblemRepository,
    shadow_problem_repo: ProblemRepository,
) -> CompareResult:
    """对比最新 control / shadow DiscoveryRun。"""
    control = control_run_repo.latest()
    shadow = shadow_run_repo.latest()
    result = CompareResult()
    if control is None and shadow is None:
        result.notes.append("no control or shadow runs")
        return result
    if control is None:
        result.notes.append("no control run")
    else:
        result.control_run_id = control.run_id
        result.control_fingerprint = control.fingerprint
        result.control_problem_ids = list(control.problem_ids)
        result.control_evidence_count = sum(
            len(p.evidence)
            for pid in control.problem_ids
            if (p := control_problem_repo.get(pid)) is not None
        )
    if shadow is None:
        result.notes.append("no shadow run")
    else:
        result.shadow_run_id = shadow.run_id
        result.shadow_fingerprint = shadow.fingerprint
        result.shadow_problem_ids = list(shadow.problem_ids)
        result.shadow_evidence_count = sum(
            len(p.evidence)
            for pid in shadow.problem_ids
            if (p := shadow_problem_repo.get(pid)) is not None
        )
    if control is not None and shadow is not None:
        result.fingerprint_match = control.fingerprint == shadow.fingerprint
        cset, sset = set(control.problem_ids), set(shadow.problem_ids)
        result.overlap_ids = sorted(cset & sset)
        result.only_control = sorted(cset - sset)
        result.only_shadow = sorted(sset - cset)
        if not result.fingerprint_match:
            result.notes.append("fingerprint mismatch — pools may differ")
    return result
