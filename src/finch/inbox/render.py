"""收件箱人类输出（不含 run_id / 节点名）。"""

from finch.inbox.models import InboxItem

_CONTENT_LABEL = {"original": "原创", "reply": "回复", "quote": "引用"}

_STATE_LABELS = {
    "COMPLETED": "已完成",
    "NEEDS_INPUT": "等待你的确认",
    "SKIPPED": "已跳过",
    "STOPPED": "已保存并退出",
    "FAILED": "运行失败",
    "BLOCKED": "运行失败",
}


def state_label(state: str) -> str:
    """把内部状态映射为面向用户的文案；未知名回退原值。"""
    return _STATE_LABELS.get(state, state)


def _item_title(item: InboxItem) -> str:
    """单条待决定项的标题：立场 decision → why_now → 正文前 40 字 → id。"""
    title = item.position.get("decision") if item.position else ""
    return title or item.why_now or item.draft[:40] or item.id


def render_daily_summary(items: list[InboxItem]) -> str:
    """daily 非 json 输出的收件箱汇总：「今天 N 条待决定」+ 逐条 `[类型] 标题`。"""
    if not items:
        return "今天没有待决定的内容。"
    lines = [f"今天 {len(items)} 条待决定。"]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. [{_CONTENT_LABEL[item.content_type]}] {_item_title(item)}")
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
