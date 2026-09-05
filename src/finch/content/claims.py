"""Claim 绑定结构门禁（contract C2）。

置信度对外表达语义（spec 7.4 / 计划 Task 1.2）：

| Confidence | 对外表达规则 |
|---|---|
| VERIFIED | 可作为事实断言，必须有直接来源 |
| SUPPORTED | 可作为有范围的事实陈述 |
| USER_CONFIRMED | 仅人工确认后产生（模型不得自行产出） |
| INFERRED | 必须使用"这表明/在这次实现中/可能"等边界语言 |
| UNKNOWN | 不得作为可发布主张 |

``assertable``（VERIFIED / SUPPORTED / USER_CONFIRMED）决定一条 claim 能否被绑定为
对外主张；INFERRED 必须先改写为带边界语言的陈述，UNKNOWN 不得进入草稿。
"""

from finch.content.models import ClaimRef, Draft
from finch.evidence.models import ClaimConfidence


def bind_claim(
    statement: str,
    evidence_card_id: str,
    confidence: ClaimConfidence,
    *,
    card_ids: set[str],
) -> ClaimRef | None:
    """仅当 claim 可对外发布时绑定为 ClaimRef。

    门禁：evidence_card_id 非空、∈ card_ids、confidence.assertable。INFERRED 未带边界
    语言、UNKNOWN 均非 assertable，因此不会被绑定。
    """
    # evidence_card_id 非空、∈ card_ids、confidence.assertable → ClaimRef；否则 None
    if not evidence_card_id:
        return None
    if evidence_card_id not in card_ids:
        return None
    if not confidence.assertable:
        return None
    return ClaimRef(
        statement=statement,
        evidence_card_id=evidence_card_id,
        confidence=confidence,
    )


def validate_draft(draft: Draft, *, card_ids: set[str]) -> list[str]:
    """返回违规描述列表（空 = 合法）。

    检查：至少 1 条 claim；每条 claim 的 evidence_card_id ∈ card_ids 且
    confidence.assertable。INFERRED（须带边界语言）与 UNKNOWN（不得发布）都被判定为
    非 assertable，无法通过门禁。
    """
    # 返回违规描述列表（空 = 合法）。检查：至少 1 条 claim；
    # 每条 claim 的 evidence_card_id ∈ card_ids 且 confidence.assertable
    violations: list[str] = []
    if not draft.claims:
        violations.append("draft must contain at least one claim")
        return violations

    for index, claim in enumerate(draft.claims):
        if claim.evidence_card_id not in card_ids:
            violations.append(
                f"claim[{index}] evidence_card_id {claim.evidence_card_id!r} not in card_ids"
            )
        if not claim.confidence.assertable:
            violations.append(
                f"claim[{index}] confidence {claim.confidence.value!r} is not assertable"
            )
    return violations
