"""从 Commit 组提取 Engineering Event 并生成 Evidence Card（spec 2.2）。"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from ..codex.runner import CodexRunner
from ..github.change_grouper import group_commits
from ..github.models import CommitDetail
from .models import ClaimConfidence, EngineeringEvent, EvidenceCard, Source

_PROMPT_PATH = Path("prompts/extract-engineering-event.md")


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


def _extract_one(
    group: list[CommitDetail],
    repo: str,
    runner: CodexRunner,
    template: str,
) -> EngineeringEvent:
    """对单个 commit 组跑一次 LLM 提取，并做 SHA 归一化 + decision 置信度钳制。"""
    prompt = template.replace("{commits}", _render_commits(group))
    event = cast(EngineeringEvent, runner.run(prompt, EngineeringEvent))
    if event.repository != repo:
        event = event.model_copy(update={"repository": repo})
    event = event.model_copy(update={"commits": _resolve_commit_shas(event.commits, group)})
    return _coerce_decision(event)


class Extractor:
    def __init__(self, runner: CodexRunner):
        self.runner = runner

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        groups = list(group_commits(commits))
        if not groups:
            return []
        template = _PROMPT_PATH.read_text()
        if len(groups) == 1:
            return [_extract_one(groups[0], repo, self.runner, template)]
        # 多组并行：每组一次独立 codex 调用；`pool.map` 保序，事件顺序 = group 顺序。
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(
                pool.map(lambda g: _extract_one(g, repo, self.runner, template), groups)
            )


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
