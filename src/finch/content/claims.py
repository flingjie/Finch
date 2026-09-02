"""Claim 绑定结构门禁（contract C2）。"""

from finch.content.models import ClaimRef, Draft
from finch.evidence.models import ClaimConfidence


def bind_claim(
    statement: str,
    evidence_card_id: str,
    confidence: ClaimConfidence,
    *,
    card_ids: set[str],
) -> ClaimRef | None:
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
