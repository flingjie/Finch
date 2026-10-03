"""ExpressionMethodService：从报告选中方法、合并建议、练习反馈。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.article.models import ArticleReport
from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import (
    ExpressionMethod,
    MergeSuggestion,
    MethodPracticeLog,
    MethodSource,
    MethodVerdict,
)
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.llm.base import StructuredInferenceRunner

_MERGE_PROMPT = """\
You suggest whether a new transferable writing method should merge into an existing
library entry. Compare by transferable ACTION (not surface labels). Return at most
3 candidates; empty list if none are synonyms.

## New method
title: {title}
why_effective: {why_effective}
when_to_use: {when_to_use}
boundaries: {boundaries}

## Existing methods
{existing}

Respond with JSON matching the schema: candidates (list of {{method_id, reason}}).
"""


class ExpressionMethodService:
    def __init__(
        self,
        methods: ExpressionMethodRepository,
        reports: ArticleReportRepository,
        runner: StructuredInferenceRunner,
    ) -> None:
        self.methods = methods
        self.reports = reports
        self.runner = runner

    def from_report(self, report: ArticleReport, index: int) -> ExpressionMethod:
        methods = report.transferable_methods
        if index < 1 or index > len(methods):
            raise ValueError(
                f"index out of range: {index} (report has {len(methods)} methods)"
            )
        tm = methods[index - 1]
        now = datetime.now(UTC)
        return ExpressionMethod(
            id=f"emethod_{uuid4().hex[:8]}",
            title=tm.method,
            why_effective=tm.why_effective_here,
            when_to_use=tm.when_to_use,
            boundaries="",
            mini_exercise=tm.mini_exercise,
            sources=[
                MethodSource(
                    report_id=report.id,
                    method_index=index,
                    excerpt="",
                    source_ref=report.source_ref,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    def suggest_merges(self, candidate: ExpressionMethod) -> MergeSuggestion:
        existing = self.methods.list_all()
        if not existing:
            return MergeSuggestion(candidates=[])
        lines = []
        for m in existing:
            lines.append(
                f"- id={m.id} | title={m.title} | when_to_use={m.when_to_use} | "
                f"boundaries={m.boundaries}"
            )
        return cast(
            MergeSuggestion,
            self.runner.run(
                _MERGE_PROMPT.format(
                    title=candidate.title,
                    why_effective=candidate.why_effective,
                    when_to_use=candidate.when_to_use,
                    boundaries=candidate.boundaries,
                    existing="\n".join(lines),
                ),
                MergeSuggestion,
            ),
        )

    def save_as_new(self, report_id: str, index: int) -> ExpressionMethod:
        report = self._require_report(report_id)
        method = self.from_report(report, index)
        self.methods.upsert(method)
        return method

    def merge_into(
        self, target_id: str, report_id: str, index: int
    ) -> ExpressionMethod:
        target = self.methods.get(target_id)
        if target is None:
            raise KeyError(target_id)
        report = self._require_report(report_id)
        draft = self.from_report(report, index)
        src = draft.sources[0]
        if any(
            s.report_id == src.report_id and s.method_index == src.method_index
            for s in target.sources
        ):
            return target
        target = target.model_copy(
            update={
                "sources": [*target.sources, src],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(target)
        return target

    def append_practice_log(
        self,
        method_id: str,
        session_id: str,
        verdict: MethodVerdict,
        note: str = "",
    ) -> ExpressionMethod:
        method = self.methods.get(method_id)
        if method is None:
            raise KeyError(method_id)
        log = MethodPracticeLog(
            session_id=session_id,
            verdict=verdict,
            note=note,
            at=datetime.now(UTC),
        )
        method = method.model_copy(
            update={
                "practice_logs": [*method.practice_logs, log],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(method)
        return method

    def _require_report(self, report_id: str) -> ArticleReport:
        report = self.reports.get(report_id)
        if report is None:
            raise KeyError(report_id)
        return report
