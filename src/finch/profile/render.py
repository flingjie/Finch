"""把已确认实践渲染成 prompt 文本块（三处读取点共用，格式唯一）。"""

from finch.profile.models import PracticeItem, PracticeProfile

NONE_MARKER = "(none)"


def _render_item(item: PracticeItem) -> str:
    head = f"- [{item.id}] ({item.status.value}) {item.domain}: {item.claim}"
    parts: list[str] = []
    if item.can_offer:
        parts.append("can_offer: " + " / ".join(item.can_offer))
    if item.boundaries:
        parts.append(f"boundaries: {item.boundaries}")
    if item.evidence_refs:
        parts.append("refs: " + ", ".join(item.evidence_refs))
    if not parts:
        return head
    return head + "\n  " + " | ".join(parts)


def render_user_practices(profile: PracticeProfile | None) -> str:
    """只渲染 confirmed 条目；None / 空画像返回 ``(none)``。"""
    if profile is None or profile.is_empty():
        return NONE_MARKER
    return "\n".join(_render_item(i) for i in profile.confirmed_items())
