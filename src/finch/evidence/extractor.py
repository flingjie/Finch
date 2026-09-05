"""从 Commit 组批量提取 Engineering Event 并生成 Evidence Card（spec 2.2）。"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from ..github.change_grouper import group_commits
from ..github.models import CommitDetail
from ..llm.base import StructuredInferenceRunner
from ..settings import ExtractionSettings
from .models import (
    Claim,
    ClaimConfidence,
    EngineeringEvent,
    EvidenceCard,
    Source,
    sanitize_model_confidence,
)

_BATCH_PROMPT_PATH = Path("prompts/extract-engineering-events-batch.md")

# 缓存版本：prompt / EngineeringEvent schema / 模型策略任一变化时手动递增，使旧缓存失效。
# v3：EngineeringEvent 新增 topics 字段，旧缓存缺少该字段需要重新提取。
_CACHE_VERSION = "v3"


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


def _sanitize_confidences(event: EngineeringEvent) -> EngineeringEvent:
    """模型输出不得自行产出 USER_CONFIRMED：逐条降级为 SUPPORTED（计划 Task 1.2）。"""
    updates: dict[str, Claim] = {}
    for field in ("problem", "decision", "result"):
        claim: Claim = getattr(event, field)
        downgraded = sanitize_model_confidence(claim.confidence)
        if downgraded is not claim.confidence:
            updates[field] = claim.model_copy(update={"confidence": downgraded})
    if not updates:
        return event
    return event.model_copy(update=updates)


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


def pack_batches(
    groups: list[list[CommitDetail]],
    template: str,
    max_prompt_bytes: int,
    max_groups_per_batch: int,
) -> list[list[list[CommitDetail]]]:
    """按最终 prompt 字节数把 groups 贪心装箱成 batch。

    保护上限是 prompt 字节数 + 单批 group 数，而不是固定批大小；group 顺序保持稳定。
    """
    batches: list[list[list[CommitDetail]]] = []
    current: list[list[CommitDetail]] = []
    for group in groups:
        candidate = [*current, group]
        prompt = template.replace("{groups}", _render_batch(candidate))
        if current and (
            len(prompt.encode("utf-8")) > max_prompt_bytes
            or len(candidate) > max_groups_per_batch
        ):
            batches.append(current)
            current = [group]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


def group_fingerprint(repo: str, group: list[CommitDetail], version: str) -> str:
    """计算一个 commit 组的稳定内容指纹（repo + 版本 + 实际进入模型的 commit 内容）。

    指纹变化即缓存失效：message / 文件 / patch 任一变化都会得到不同指纹。
    """
    payload = {
        "repo": repo,
        "version": version,
        "commits": [
            {
                "sha": c.sha,
                "message": c.message,
                "files": [
                    {
                        "filename": f.filename,
                        "status": f.status,
                        "additions": f.additions,
                        "deletions": f.deletions,
                        "patch": f.patch,
                    }
                    for f in c.files
                ],
            }
            for c in group
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


class ExtractionCache:
    """group 指纹 → 已验证 EngineeringEvent 的持久化缓存（单 JSON 文件）。"""

    def __init__(self, path: Path):
        self.path = path
        self._data = self._load()
        self._lock = threading.Lock()

    def _load(self) -> dict[str, str]:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def get(self, fingerprint: str) -> EngineeringEvent | None:
        with self._lock:
            raw = self._data.get(fingerprint)
        if raw is None:
            return None
        try:
            return EngineeringEvent.model_validate_json(raw)
        except Exception:  # noqa: BLE001 - 缓存损坏时按 miss 处理
            return None

    def put(self, fingerprint: str, event: EngineeringEvent) -> None:
        with self._lock:
            self._data[fingerprint] = event.model_dump_json()

    def save(self) -> None:
        with self._lock:
            self._flush_unlocked()

    def persist_many(self, items: list[tuple[str, EngineeringEvent]]) -> None:
        """原子写入多条并落盘；补偿/并发批失败时保留已成功 group。"""
        if not items:
            return
        with self._lock:
            for fingerprint, event in items:
                self._data[fingerprint] = event.model_dump_json()
            self._flush_unlocked()

    def _flush_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2))


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
    """补齐 repository、归一化 SHA、降级模型产出的 USER_CONFIRMED、钳制 decision 置信度。"""
    if event.repository != repo:
        event = event.model_copy(update={"repository": repo})
    event = event.model_copy(update={"commits": _resolve_commit_shas(event.commits, group)})
    event = _sanitize_confidences(event)
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
    def __init__(
        self,
        runner: StructuredInferenceRunner,
        settings: ExtractionSettings | None = None,
        cache_path: Path | None = None,
    ):
        self.runner = runner
        self.settings = settings or ExtractionSettings()
        self.cache = ExtractionCache(cache_path) if cache_path is not None else None

    def extract(self, commits: list[CommitDetail], repo: str) -> list[EngineeringEvent]:
        """薄封装：先分组再委托 extract_grouped（保住 ``github reflect`` CLI）。"""
        return self.extract_grouped(list(group_commits(commits)), repo)

    def extract_grouped(
        self, groups: list[list[CommitDetail]], repo: str
    ) -> list[EngineeringEvent]:
        """对预分组 group 批量提取事件，缺失组最多局部补偿一次（阶段 A：缓存键为内容指纹）。

        事件顺序 = 输入 group 顺序。
        """
        if not groups:
            return []
        template = _BATCH_PROMPT_PATH.read_text()

        # 指纹缓存查找：命中直接复用，未命中进入提取列表。
        events: dict[int, EngineeringEvent] = {}
        miss_indices: list[int] = []
        for i, group in enumerate(groups):
            cached = (
                self.cache.get(group_fingerprint(repo, group, _CACHE_VERSION))
                if self.cache is not None
                else None
            )
            if cached is not None:
                events[i] = cached
            else:
                miss_indices.append(i)

        if miss_indices:
            miss_groups = [groups[i] for i in miss_indices]
            fresh = self._extract_groups(miss_groups, repo, template)
            for local_i, global_i in enumerate(miss_indices):
                events[global_i] = fresh[local_i]
                if self.cache is not None:
                    self.cache.put(
                        group_fingerprint(repo, miss_groups[local_i], _CACHE_VERSION),
                        fresh[local_i],
                    )
            if self.cache is not None:
                self.cache.save()

        return [events[i] for i in range(len(groups))]

    def _extract_groups(
        self,
        groups: list[list[CommitDetail]],
        repo: str,
        template: str,
    ) -> list[EngineeringEvent]:
        batches = pack_batches(
            groups,
            template,
            self.settings.max_prompt_bytes,
            self.settings.max_groups_per_batch,
        )
        if len(batches) == 1:
            return self._extract_group_batch(batches[0], repo, template)

        # 多批：最多两路并发（配置化），pool.map 保序。
        workers = min(len(batches), self.settings.max_concurrent_batches)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batch_events = list(
                pool.map(lambda b: self._extract_group_batch(b, repo, template), batches)
            )
        return [event for batch in batch_events for event in batch]

    def _persist_cached(
        self,
        repo: str,
        groups: list[list[CommitDetail]],
        events_by_idx: dict[int, EngineeringEvent],
    ) -> None:
        if self.cache is None or not events_by_idx:
            return
        self.cache.persist_many(
            [
                (group_fingerprint(repo, groups[i], _CACHE_VERSION), event)
                for i, event in events_by_idx.items()
            ]
        )

    def _extract_group_batch(
        self,
        groups: list[list[CommitDetail]],
        repo: str,
        template: str,
    ) -> list[EngineeringEvent]:
        valid, missing = self._extract_valid(groups, repo, template)
        # 补偿超时/失败不得丢掉已成功组：先落盘，下次 daily 只补 miss。
        self._persist_cached(repo, groups, valid)
        if missing:
            # 局部补偿：只重跑缺失/无效组，最多一次，不重跑已成功组。
            recovery = [groups[i] for i in missing]
            rvalid, _ = self._extract_valid(recovery, repo, template)
            recovered: dict[int, EngineeringEvent] = {}
            for local_i, orig_i in enumerate(missing):
                if local_i in rvalid:
                    valid[orig_i] = rvalid[local_i]
                    recovered[orig_i] = rvalid[local_i]
            self._persist_cached(repo, groups, recovered)
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
            self.runner.run(prompt, BatchExtractionOutput, timeout=self.settings.timeout_seconds),
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
                topics=ev.topics,
            ))
        cards.append(EvidenceCard(
            id=f"ev_{ev.id}_decision",
            event_id=ev.id,
            claim=ev.decision.statement,
            sources=[Source(type="commit", url=base + c) for c in ev.commits],
            confidence=ev.decision.confidence,
            publishable=True,
            topics=ev.topics,
        ))
    return cards
