"""从 Commit 组批量提取 Engineering Event 并生成 Evidence Card（spec 2.2）。"""

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from ..github.change_grouper import group_commits
from ..github.models import CommitDetail
from ..llm.base import StructuredInferenceRunner
from .models import ClaimConfidence, EngineeringEvent, EvidenceCard, Source

_BATCH_PROMPT_PATH = Path("prompts/extract-engineering-events-batch.md")
_EXTRACTION_TIMEOUT = 120.0


class ExtractedGroup(BaseModel):
    group_id: str
    event: EngineeringEvent


class BatchExtractionOutput(BaseModel):
    items: list[ExtractedGroup]


class DuplicateExtractionGroupError(RuntimeError):
    """批量输出包含重复 group_id。"""


class IncompleteBatchExtractionError(RuntimeError):
    """补偿后仍缺失 group。"""


def _coerce_decision(event: EngineeringEvent) -> EngineeringEvent:
    """decision（动机）在本管线中无 PR/Issue 证据，不得高于 INFERRED（spec 7.4）。"""
    if event.decision.confidence in {ClaimConfidence.VERIFIED, ClaimConfidence.SUPPORTED}:
        decision = event.decision.model_copy(update={"confidence": ClaimConfidence.INFERRED})
        return event.model_copy(update={"decision": decision})
    return event


def _render_commits(commits: list[CommitDetail]) -> str:
    lines: list[str] = []
    for c in commits:
        lines.append(f"- {c.sha[:8]} {c.message[:200]}")
        for f in c.files[:12]:
            lines.append(f"    {f.status} {f.filename} (+{f.additions}/-{f.deletions})")
            if f.patch:
                snippet = "\n".join(f.patch.splitlines()[:10])
                lines.append(f"    ```diff\n{snippet}\n    ```")
    return "\n".join(lines)


def _render_batch(groups: list[list[CommitDetail]]) -> str:
    """把全部 group 渲染为带 group_id 标题的分节文本，一次提交。"""
    sections = [f"### Group g_{i}\n{_render_commits(group)}" for i, group in enumerate(groups)]
    return "\n\n".join(sections)


def _resolve_commit_shas(refs: list[str], group: list[CommitDetail]) -> list[str]:
    """Map LLM-returned commit refs back to the full SHAs in the input group.

    The extraction prompt only shows 8-char prefixes, so the model typically echoes
    those, sometimes together with the commit message. Exact and unambiguous prefix
    matches are canonicalized to full SHAs; unmatched refs are kept as-is so the
    safety scanner can still flag hallucinations.
    """
    full_shas = {c.sha for c in group}
    resolved: list[str] = []
    for ref in refs:
        candidate = ref.strip().split()[0] if ref.strip() else ""
        if not candidate:
            continue
        if candidate in full_shas:
            resolved.append(candidate)
            continue
        matches = [sha for sha in full_shas if sha.startswith(candidate)]
        if len(matches) == 1:
            resolved.append(matches[0])
        else:
            resolved.append(candidate)
    return resolved


def _parse_group_index(group_id: str) -> int | None:
    if not group_id.startswith("g_"):
        return None
    try:
        return int(group_id[2:])
    except ValueError:
        return None


def _finalize_event(
    event: EngineeringEvent,
    repo: str,
    group: list[CommitDetail],
) -> EngineeringEvent:
    """补齐 repository、归一化 SHA、钳制 decision 置信度。"""
    if event.repository != repo:
        event = event.model_copy(update={"repository": repo})
    event = event.model_copy(update={"commits": _resolve_commit_shas(event.commits, group)})
    return _coerce_decision(event)


def _validate_batch(
    output: BatchExtractionOutput,
    groups: list[list[CommitDetail]],
    repo: str,
) -> tuple[dict[int, EngineeringEvent], list[int]]:
    """校验批量输出，返回 (按组序号的合法事件, 缺失/无效的组序号)。

    - 重复 group_id → 抛 DuplicateExtractionGroupError
    - 未知 group_id → 拒绝（忽略，不进入下游）
    - 缺失 group_id → 进入缺失列表（由调用方补偿）
    - 跨组/未知 SHA → 该组视为无效，进入缺失列表
    """
    group_shas = [{c.sha for c in group} for group in groups]

    seen: set[str] = set()
    valid: dict[int, EngineeringEvent] = {}
    for item in output.items:
        if item.group_id in seen:
            raise DuplicateExtractionGroupError(
                f"duplicate group_id in batch output: {item.group_id!r}"
            )
        seen.add(item.group_id)

        idx = _parse_group_index(item.group_id)
        if idx is None or idx >= len(groups):
            continue  # 未知 group_id → 拒绝
        event = _finalize_event(item.event, repo, groups[idx])
        if any(sha not in group_shas[idx] for sha in event.commits):
            continue  # 跨组/未知 SHA → 无效
        valid[idx] = event

    missing = [i for i in range(len(groups)) if i not in valid]
    return valid, missing


class Extractor:
    def __init__(self, runner: StructuredInferenceRunner):
        self.runner = runner

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        """一次性 batch 提取所有 commit 组的事件，缺失组最多局部补偿一次。

        保留 Python 的确定性分组，只合并模型调用；事件顺序 = 原始 group 顺序。
        """
        groups = list(group_commits(commits))
        if not groups:
            return []
        template = _BATCH_PROMPT_PATH.read_text()

        valid, missing = self._extract_valid(groups, repo, template)
        if missing:
            # 局部补偿：只重跑缺失/无效组，最多一次，不重跑已成功组。
            recovery = [groups[i] for i in missing]
            rvalid, _ = self._extract_valid(recovery, repo, template)
            for local_i, orig_i in enumerate(missing):
                if local_i in rvalid:
                    valid[orig_i] = rvalid[local_i]
            still_missing = [i for i in missing if i not in valid]
            if still_missing:
                raise IncompleteBatchExtractionError(
                    f"extraction incomplete: missing groups "
                    f"{[f'g_{i}' for i in still_missing]}"
                )

        return [valid[i] for i in range(len(groups))]

    def _extract_valid(
        self,
        groups: list[list[CommitDetail]],
        repo: str,
        template: str,
    ) -> tuple[dict[int, EngineeringEvent], list[int]]:
        prompt = template.replace("{groups}", _render_batch(groups))
        output = cast(
            BatchExtractionOutput,
            self.runner.run(prompt, BatchExtractionOutput, timeout=_EXTRACTION_TIMEOUT),
        )
        return _validate_batch(output, groups, repo)


def build_cards(events: list[EngineeringEvent]) -> list[EvidenceCard]:
    cards: list[EvidenceCard] = []
    for ev in events:
        base = f"https://github.com/{ev.repository}/commit/"
        for claim, label in ((ev.problem, "problem"), (ev.result, "result")):
            cards.append(EvidenceCard(
                id=f"ev_{ev.id}_{label}",
                event_id=ev.id,
                claim=claim.statement,
                sources=[Source(type="commit", url=base + c) for c in ev.commits],
                confidence=claim.confidence,
                publishable=True,
                topics=[],
            ))
        cards.append(EvidenceCard(
            id=f"ev_{ev.id}_decision",
            event_id=ev.id,
            claim=ev.decision.statement,
            sources=[Source(type="commit", url=base + c) for c in ev.commits],
            confidence=ev.decision.confidence,
            publishable=True,
            topics=[],
        ))
    return cards
