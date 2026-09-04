"""一次 batch Codex judge：把 ranked pairs 与 evidence cards 交给模型评分。"""

import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from ..codex.runner import CodexRunner
from ..twitter.models import DiscussionCandidate
from .models import EvidenceCard, JudgeScores, RankedCandidate

_PROMPT_PATH = Path("prompts/match-discussion.md")

# match_evidence 是纯结构化 rerank（10 候选 × 已召回卡片），裁剪后应在几十秒内完成；
# 用更短的超时快速失败，避免套用 codex 的 600s 默认值空等。
_MATCH_TIMEOUT_SECONDS = 90.0


class BatchJudgeItem(BaseModel):
    candidate_id: str
    scores: JudgeScores


class BatchJudgeOutput(BaseModel):
    items: list[BatchJudgeItem]


def _to_json_text(models: Sequence[BaseModel]) -> str:
    return json.dumps([m.model_dump(mode="json") for m in models])


def _select_judge_context(
    ranked: list[RankedCandidate],
    candidates: list[DiscussionCandidate],
    cards: list[EvidenceCard],
) -> tuple[list[DiscussionCandidate], list[EvidenceCard]]:
    """只保留 ranked 可达的 candidate/card，并校验引用完整性。

    进入 LLM prompt 的 candidate/card 必须能从 ranked pairs 到达；ranked 引用不存在的
    candidate 或 card 时抛 ValueError，而不是让模型猜。
    """
    candidate_ids = {item.candidate_id for item in ranked}
    card_ids = {card_id for item in ranked for card_id in item.card_ids}

    known_candidates = {c.id for c in candidates}
    known_cards = {c.id for c in cards}
    missing_candidates = candidate_ids - known_candidates
    if missing_candidates:
        raise ValueError(f"ranked references unknown candidates: {sorted(missing_candidates)!r}")
    missing_cards = card_ids - known_cards
    if missing_cards:
        raise ValueError(f"ranked references unknown cards: {sorted(missing_cards)!r}")

    selected_candidates = [c for c in candidates if c.id in candidate_ids]
    selected_cards = [c for c in cards if c.id in card_ids]
    return selected_candidates, selected_cards


def judge_batch(
    runner: CodexRunner,
    ranked: list[RankedCandidate],
    candidates: list[DiscussionCandidate],
    cards: list[EvidenceCard],
    *,
    timeout: float = _MATCH_TIMEOUT_SECONDS,
) -> BatchJudgeOutput:
    """一次 Codex 调用完成所有 ranked pairs 的评分。

    ranked 为空时不调用 runner，直接返回空 items（0 次调用）。仅把 ranked 可达的
    candidate/card 序列化进 prompt（建立「进入 prompt 的数据必须可从 ranked 到达」的
    不变量），避免把未召回的全量推文与卡片塞给模型。
    """
    if not ranked:
        return BatchJudgeOutput(items=[])
    selected_candidates, selected_cards = _select_judge_context(ranked, candidates, cards)
    prompt = _PROMPT_PATH.read_text().format(
        pairs=_to_json_text(ranked),
        candidates=_to_json_text(selected_candidates),
        cards=_to_json_text(selected_cards),
    )
    return cast(BatchJudgeOutput, runner.run(prompt, BatchJudgeOutput, timeout=timeout))
