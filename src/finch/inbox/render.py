"""收件箱人类输出（不含 run_id / 节点名）。"""

from finch.inbox.models import InboxItem, InboxTrack

_TRACK_LABEL = {InboxTrack.ORIGINAL: "原创", InboxTrack.ENGAGEMENT: "互动"}
_CONTENT_LABEL = {"original": "原创", "reply": "回复", "quote": "引用"}


def render_inbox(items: list[InboxItem]) -> str:
    """「今天 N 条待决定」汇总。"""
    if not items:
        return "今天没有待决定的内容。"
    lines = [f"今天 {len(items)} 条待决定。", ""]
    for i, item in enumerate(items, start=1):
        title = item.position.get("decision") if item.position else ""
        title = title or item.why_now or item.draft[:40] or item.id
        lines.append(f"{i}. [{_CONTENT_LABEL[item.content_type]}] {title}")
    lines += ["", "finch next     # 看第 1 条"]
    return "\n".join(lines)


def render_card(item: InboxItem) -> str:
    """单条决策卡。"""
    lines = [f"[{_CONTENT_LABEL[item.content_type]}] {item.id}"]
    if item.source_refs:
        lines.append("来源：" + " ".join(item.source_refs))
    if item.why_now:
        lines.append(f"为什么现在：{item.why_now}")
    if item.position:
        lines.append(
            f"立场：{item.position.get('decision') or item.position.get('claim') or '（待确认）'}"
        )
    if item.must_ask:
        lines.append("需要你确认：" + "；".join(item.ask_reasons))
    lines += ["", item.draft, "", f"decide {item.id} --action accept|skip"]
    return "\n".join(lines)
