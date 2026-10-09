"""Notion 素材块映射：toggle 块 ↔ 快照 / 追加块构造 / 区域切分。

一条素材 = 父页面里的一个 toggle（``<details>``）块：summary 是标题，children 是正文
（原始记录 + 可选「我的感触」），讨论后追加「Finch 讨论记录」区。Finch 只追加不重写。
"""

from __future__ import annotations

from datetime import datetime

from finch.materials.models import DiscussionRecord, MaterialSnapshot, source_hash_for

# 正文区域标记（canonical，不改变）。
REGION_RAW = "原始记录"
REGION_REFLECTION = "我的感触"
REGION_FINCH = "Finch 讨论记录"


def text_rich(content: str) -> list[dict]:
    """一段纯文本的 rich_text 值。"""
    return [{"type": "text", "text": {"content": content}}]


def paragraph_block(text: str) -> dict:
    """一个 paragraph 块。"""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": text_rich(text)}}


def block_plain_text(block: dict) -> str:
    """取块内纯文本（heading/paragraph/toggle/bulleted/… 的 rich_text）。

    兼容 Notion 响应（带 ``plain_text``）与本地构造块（只有 ``text.content``）。
    """
    btype = block.get("type")
    if not btype:
        return ""
    value = block.get(btype) or {}
    rich = value.get("rich_text") or []
    return "".join(_rich_item_text(item) for item in rich)


def _rich_item_text(item: dict) -> str:
    content = (item.get("text") or {}).get("content")
    if content:
        return content
    return item.get("plain_text") or ""


def flatten_blocks(blocks: list[dict]) -> str:
    """块 → 纯文本（空文本略过，用换行连接）。"""
    return "\n".join(text for block in blocks if (text := block_plain_text(block)))


def user_region_text(raw: str, reflection: str | None) -> str:
    """用户区正文（原始记录 + 我的感触）的规范文本。"""
    parts = [raw.strip()]
    if reflection and reflection.strip():
        parts.append(reflection.strip())
    return "\n".join(part for part in parts if part)


def material_toggle_block(title: str, body_text: str, reflection: str | None) -> dict:
    """构造一条素材的 toggle 块：summary=标题，children=正文 + 可选「我的感触」。"""
    children: list[dict] = []
    if body_text.strip():
        children.append(paragraph_block(body_text))
    if reflection and reflection.strip():
        children.append(
            {
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": text_rich(REGION_REFLECTION)},
            }
        )
        children.append(paragraph_block(reflection))
    return {
        "object": "block",
        "type": "toggle",
        "toggle": {"rich_text": text_rich(title)},
        "children": children,
    }


def blocks_for_discussion(discussion_id: str, record: DiscussionRecord) -> list[dict]:
    """讨论回写块：块 0 是标记标题，其后是判断 / AI 提议 / 未解决 / 行动段落。"""
    blocks: list[dict] = [
        {
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": text_rich(f"{REGION_FINCH} #{discussion_id}")},
        }
    ]
    if record.user_judgment.strip():
        blocks.append(paragraph_block(f"判断：{record.user_judgment}"))
    for proposal in record.ai_proposals:
        blocks.append(paragraph_block(f"AI 提议：{proposal}"))
    for question in record.open_questions:
        blocks.append(paragraph_block(f"未解决：{question}"))
    if record.action.strip():
        blocks.append(paragraph_block(f"行动：{record.action}"))
    return blocks


def toggle_to_snapshot(page: dict, block: dict, children: list[dict]) -> MaterialSnapshot:
    """toggle 块 + children → 本地快照（source_hash 只对用户区算）。"""
    raw_blocks, reflection = _split_user_region(children)
    extractable = user_region_text(flatten_blocks(raw_blocks), reflection)
    return MaterialSnapshot(
        block_id=block["id"],
        page_id=page["id"],
        page_url=page.get("url") or f"https://www.notion.so/{page['id']}",
        title=block_plain_text(block),
        raw_body_blocks=list(children),
        extractable_text=extractable,
        user_reflection=reflection,
        discussed=any(block_plain_text(b).startswith(REGION_FINCH) for b in children),
        remote_edited_at=_parse_iso(page.get("last_edited_time")),
        source_hash=source_hash_for(extractable),
    )


def _split_user_region(children: list[dict]) -> tuple[list[dict], str | None]:
    """把正文切成用户区（原始记录 + 我的感触）与 Finch 区，排除区域标记标题。

    无 Finch 区时整段视为用户区；无「我的感触」标记时感触为 None。
    """
    user_blocks: list[dict] = []
    for block in children:
        if block_plain_text(block).startswith(REGION_FINCH):
            break
        user_blocks.append(block)

    raw_blocks: list[dict] = []
    reflection_blocks: list[dict] = []
    in_reflection = False
    for block in user_blocks:
        text = block_plain_text(block)
        if text == REGION_RAW:
            continue
        if text == REGION_REFLECTION:
            in_reflection = True
            continue
        (reflection_blocks if in_reflection else raw_blocks).append(block)

    reflection = flatten_blocks(reflection_blocks) if reflection_blocks else None
    return raw_blocks, reflection


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
