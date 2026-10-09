"""ExpressionMethodService：从报告选中方法、合并建议、练习反馈、发现解析。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from finch.article.models import ArticleReport, TransferableMethod
from finch.article.repository import ArticleReportRepository
from finch.expression_methods.models import (
    ExpressionMethod,
    MergeSuggestion,
    MethodPracticeLog,
    MethodSource,
    MethodVerdict,
    ReplyMethodVerdict,
)
from finch.expression_methods.repository import ExpressionMethodRepository
from finch.expression_methods.seed_cw48 import CW48_SEED, SOURCE_NOTE
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

_LIBRARY_CAP = 10
_REPLY_FORM = "reply"


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
                    why_effective_here=tm.why_effective_here,
                    content_hash=report.content_hash,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    def find_by_source(
        self, report_id: str, method_index: int
    ) -> ExpressionMethod | None:
        for m in self.methods.list_all():
            for s in m.sources:
                if s.report_id == report_id and s.method_index == method_index:
                    return m
        return None

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
        existing = self.find_by_source(report_id, index)
        if existing is not None:
            return existing
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

    def seed_cw48(
        self, *, force: bool = False
    ) -> tuple[list[str], list[str], list[str]]:
        """幂等导入《精简写作》48 条种子方法，返回 (created, merged, skipped)。

        - ID 已存在且未 ``force`` → 跳过（多次导入无重复）。
        - 与现有方法精确 ``title`` 重合且未 ``force`` → 把书籍来源写入该方法的
          ``source_note``，不新建同义方法。
        - 否则新建（或 ``force`` 覆盖）对应 EP-CW-xxx。
        """
        now = datetime.now(UTC)
        existing = {m.id: m for m in self.methods.list_all()}
        by_title: dict[str, ExpressionMethod] = {}
        for m in existing.values():
            by_title.setdefault(m.title, m)

        created: list[str] = []
        merged: list[str] = []
        skipped: list[str] = []
        for row in CW48_SEED:
            mid = row["id"]
            if mid in existing and not force:
                skipped.append(mid)
                continue
            dup = by_title.get(row["title"])
            if dup is not None and dup.id != mid and not force:
                if self._has_book_source(dup, mid):
                    skipped.append(mid)
                else:
                    self.methods.upsert(self._attach_book_source(dup, mid))
                    merged.append(dup.id)
                continue
            self.methods.upsert(self._build_seed_method(row, now))
            created.append(mid)

        return created, merged, skipped

    def _build_seed_method(self, row: dict, now: datetime) -> ExpressionMethod:
        return ExpressionMethod(
            id=row["id"],
            title=row["title"],
            why_effective=row["why_effective"],
            when_to_use=row["when_to_use"],
            boundaries=row["boundaries"],
            mini_exercise=row["mini_exercise"],
            method_type=row["method_type"],
            dimension=row["dimension"],
            chapter=row["chapter"],
            evidence_status="user_supplied_toc_summary",
            executable=row["executable"],
            source_note=SOURCE_NOTE,
            created_at=now,
            updated_at=now,
        )

    def _attach_book_source(
        self, method: ExpressionMethod, seed_id: str
    ) -> ExpressionMethod:
        marker = f"《精简写作》目录（对应 {seed_id}）"
        note = method.source_note
        if marker in note:
            return method
        note = f"{note}\n{marker}；{SOURCE_NOTE}" if note else f"{marker}；{SOURCE_NOTE}"
        return method.model_copy(
            update={"source_note": note, "updated_at": datetime.now(UTC)}
        )

    def _has_book_source(self, method: ExpressionMethod, seed_id: str) -> bool:
        marker = f"《精简写作》目录（对应 {seed_id}）"
        return marker in method.source_note

    def append_practice_log(
        self,
        method_id: str,
        session_id: str,
        verdict: MethodVerdict,
        note: str = "",
        conditions: str = "",
    ) -> ExpressionMethod:
        method = self.methods.get(method_id)
        if method is None:
            raise KeyError(method_id)
        log = MethodPracticeLog(
            session_id=session_id,
            verdict=verdict,
            note=note,
            at=datetime.now(UTC),
            conditions=conditions,
        )
        method = method.model_copy(
            update={
                "practice_logs": [*method.practice_logs, log],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(method)
        return method

    def append_reply_log(
        self,
        method_id: str,
        *,
        draft_ref: str,
        verdict: ReplyMethodVerdict,
        note: str = "",
        conditions: str = "",
        question_asked: str = "",
        response: str = "",
        follow_up_action: str = "",
    ) -> ExpressionMethod:
        """记录一条回复复用反馈（form=reply + 草稿引用），写入方法 practice_logs。

        ``conditions`` 记录这次在什么条件下起作用；``question_asked`` / ``response`` /
        ``follow_up_action`` 记录一次回复引发的具体交流（问了什么、对方回应、后续行动），
        不直接归因于某种措辞。
        """
        method = self.methods.get(method_id)
        if method is None:
            raise KeyError(method_id)
        log = MethodPracticeLog(
            session_id=draft_ref,
            verdict=verdict,
            note=note,
            at=datetime.now(UTC),
            form="reply",
            draft_ref=draft_ref,
            conditions=conditions,
            question_asked=question_asked,
            response=response,
            follow_up_action=follow_up_action,
        )
        method = method.model_copy(
            update={
                "practice_logs": [*method.practice_logs, log],
                "updated_at": datetime.now(UTC),
            }
        )
        self.methods.upsert(method)
        return method

    def resolve_methods_for_discovery(
        self,
        *,
        method_ids: list[str] | None = None,
        report_id: str | None = None,
        use_library: bool = False,
    ) -> list[ExpressionMethod]:
        """Load methods for idea-discovery. Fail closed on missing explicit IDs.

        Ephemeral cards from a report (when no saved methods cite it) get ids
        ``emethod_ephemeral_<index>`` and are not persisted.
        """
        by_id: dict[str, ExpressionMethod] = {}

        for mid in method_ids or []:
            m = self.methods.get(mid)
            if m is None:
                raise KeyError(mid)
            by_id[m.id] = m

        if report_id is not None:
            cited = [
                m
                for m in self.methods.list_all()
                if any(s.report_id == report_id for s in m.sources)
            ]
            if cited:
                for m in cited:
                    by_id[m.id] = m
            else:
                report = self._require_report(report_id)
                for i, tm in enumerate(report.transferable_methods, start=1):
                    ephemeral = self._ephemeral_from_transferable(report, i, tm)
                    by_id[ephemeral.id] = ephemeral

        if use_library:
            library = sorted(
                self.methods.list_all(),
                key=lambda m: m.updated_at,
                reverse=True,
            )[:_LIBRARY_CAP]
            for m in library:
                by_id[m.id] = m

        return list(by_id.values())

    def resolve_reply_methods(
        self,
        *,
        method_ids: list[str] | None = None,
        report_id: str | None = None,
        use_library: bool = False,
    ) -> list[ExpressionMethod]:
        """Load reply-applicable methods for reply crafting. Fail closed on missing explicit IDs.

        显式 ``method_ids`` / ``report_id`` 不筛 applicable_forms（用户明确指定）；仅
        ``use_library`` 才筛 ``"reply" in applicable_forms`` 并取最近 ``_LIBRARY_CAP`` 个。
        """
        by_id: dict[str, ExpressionMethod] = {}

        for mid in method_ids or []:
            m = self.methods.get(mid)
            if m is None:
                raise KeyError(mid)
            by_id[m.id] = m

        if report_id is not None:
            cited = [
                m
                for m in self.methods.list_all()
                if any(s.report_id == report_id for s in m.sources)
            ]
            if cited:
                for m in cited:
                    by_id[m.id] = m
            else:
                report = self._require_report(report_id)
                for i, tm in enumerate(report.transferable_methods, start=1):
                    ephemeral = self._ephemeral_from_transferable(report, i, tm)
                    by_id[ephemeral.id] = ephemeral

        if use_library:
            library = sorted(
                [m for m in self.methods.list_all() if _REPLY_FORM in m.applicable_forms],
                key=lambda m: m.updated_at,
                reverse=True,
            )[:_LIBRARY_CAP]
            for m in library:
                by_id[m.id] = m

        return list(by_id.values())

    def soft_filter_for_facts(
        self, methods: list[ExpressionMethod], facts: list[str]
    ) -> list[ExpressionMethod]:
        """Prefer methods whose required_material keywords appear in facts.

        Lenient: only drop when required_material is non-empty and no token
        overlaps; empty required_material always kept.
        """
        if not methods:
            return []
        blob = "\n".join(facts).casefold()
        kept: list[ExpressionMethod] = []
        for m in methods:
            req = (m.required_material or "").strip()
            if not req:
                kept.append(m)
                continue
            tokens = [t for t in req.replace("，", " ").replace(",", " ").split() if t]
            if not tokens or any(t.casefold() in blob for t in tokens):
                kept.append(m)
        return kept if kept else list(methods)

    def _ephemeral_from_transferable(
        self, report: ArticleReport, index: int, tm: TransferableMethod
    ) -> ExpressionMethod:
        now = datetime.now(UTC)
        return ExpressionMethod(
            id=f"emethod_ephemeral_{index}",
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
                    why_effective_here=tm.why_effective_here,
                    content_hash=report.content_hash,
                )
            ],
            created_at=now,
            updated_at=now,
        )

    def _require_report(self, report_id: str) -> ArticleReport:
        report = self.reports.get(report_id)
        if report is None:
            raise KeyError(report_id)
        return report
