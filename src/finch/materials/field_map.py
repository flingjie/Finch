"""Notion 素材库的属性名常量 + 页面 ↔ 快照 / create / patch / query 映射。

字段所有权：用户维护标题 / 来源链接 / 主题标签 / 正文；Finch 只维护 ``Finch 操作标识``
（create 去重键）与 ``已讨论``（讨论回写后更新），只追加正文，不重写用户区。
"""

from __future__ import annotations

from datetime import datetime, timedelta

from finch.materials.models import DiscussionRecord, MaterialSnapshot, source_hash_for

# 属性名（canonical，不改变；doctor 用它核对用户库的字段）。
PROP_TITLE = "标题"
PROP_SOURCE_URL = "来源链接"
PROP_TAGS = "主题标签"
PROP_DISCUSSED = "已讨论"
PROP_FINCH_OP = "Finch 操作标识"

# 正文区域标记（canonical，不改变）。
REGION_RAW = "原始记录"
REGION_REFLECTION = "我的感触"
REGION_FINCH = "Finch 讨论记录"

# 创建时的可写属性（创建/修改时间是只读系统属性，读回即可）。
WRITABLE_PROPERTIES = (PROP_TITLE, PROP_SOURCE_URL, PROP_TAGS, PROP_DISCUSSED, PROP_FINCH_OP)


def text_rich(content: str) -> list[dict]:
    """一段纯文本的 rich_text 值。"""
    return [{"type": "text", "text": {"content": content}}]


def paragraph_block(text: str) -> dict:
    """一个 paragraph 块。"""
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": text_rich(text)}}


def block_plain_text(block: dict) -> str:
    """取块内纯文本（heading/paragraph/bulleted/numbered/to_do/quote/callout 的 rich_text）。

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


def properties_for_create(
    title: str, source_urls: list[str], tags: list[str], operation_id: str
) -> dict:
    """构建创建页的可写属性（来源链接为空时省略；创建/修改时间由 Notion 读回）。"""
    props: dict = {
        PROP_TITLE: {"title": text_rich(title)},
        PROP_TAGS: {"multi_select": [{"name": tag} for tag in tags]},
        PROP_DISCUSSED: {"checkbox": False},
        PROP_FINCH_OP: {"rich_text": text_rich(operation_id)},
    }
    if source_urls:
        props[PROP_SOURCE_URL] = {"url": source_urls[0]}
    return props


def properties_for_patch(discussed: bool | None = None, tags: list[str] | None = None) -> dict:
    """构建局部补丁（只含给定字段）。"""
    props: dict = {}
    if discussed is not None:
        props[PROP_DISCUSSED] = {"checkbox": discussed}
    if tags is not None:
        props[PROP_TAGS] = {"multi_select": [{"name": tag} for tag in tags]}
    return props


def blocks_for_create(body_text: str, reflection: str | None) -> list[dict]:
    """创建页时的用户区正文块（原始记录 + 可选我的感触；Finch 区留空）。"""
    children: list[dict] = [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": text_rich(REGION_RAW)}}
    ]
    if body_text.strip():
        children.append(paragraph_block(body_text))
    if reflection and reflection.strip():
        children.append(
            {
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": text_rich(REGION_REFLECTION)},
            }
        )
        children.append(paragraph_block(reflection))
    return children


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


def query_filter_for_operation(operation_id: str) -> dict:
    """按 ``Finch 操作标识`` 精确查页（create 去重用）。"""
    return {"property": PROP_FINCH_OP, "rich_text": {"equals": operation_id}}


def query_filter_incremental(
    committed_boundary: datetime | None, overlap: timedelta
) -> dict | None:
    """增量扫描的时间过滤（重叠时间窗，避免边界遗漏）；无水位线时返回 None（全量）。"""
    if committed_boundary is None:
        return None
    start = committed_boundary - overlap
    return {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": start.isoformat()}}


def sorts_incremental() -> list[dict]:
    """按修改时间升序排序（配合增量水位线推进）。"""
    return [{"timestamp": "last_edited_time", "direction": "ascending"}]


def page_to_snapshot(page: dict, children: list[dict]) -> MaterialSnapshot:
    """Notion 页 + 正文块 → 本地快照（source_hash 只对用户区算）。"""
    props = page.get("properties", {})
    title = _title_text(props.get(PROP_TITLE))
    source_urls = _url_value(props.get(PROP_SOURCE_URL))
    tags = [
        item.get("name", "")
        for item in (props.get(PROP_TAGS, {}).get("multi_select") or [])
    ]
    discussed = bool((props.get(PROP_DISCUSSED) or {}).get("checkbox"))
    last_edited = _parse_iso(page.get("last_edited_time"))

    raw_blocks, reflection = _split_user_region(children)
    extractable_text = user_region_text(flatten_blocks(raw_blocks), reflection)
    return MaterialSnapshot(
        notion_page_id=page["id"],
        page_url=page.get("url") or f"https://www.notion.so/{page['id']}",
        title=title,
        raw_body_blocks=list(children),
        extractable_text=extractable_text,
        user_reflection=reflection,
        source_urls=source_urls,
        tags=tags,
        discussed=discussed,
        remote_edited_at=last_edited,
        source_hash=source_hash_for(extractable_text),
    )


def _split_user_region(children: list[dict]) -> tuple[list[dict], str | None]:
    """把正文切成用户区（原始记录 + 我的感触）与 Finch 区，排除区域标记标题。

    无 ``Finch 讨论记录`` 标记时整页视为用户区；无 ``我的感触`` 标记时感触为 None。
    """
    user_blocks: list[dict] = []
    for block in children:
        # Finch 区标记形如「Finch 讨论记录 #<discussion_id>」，按前缀识别。
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


def _title_text(prop: dict | None) -> str:
    if not prop:
        return ""
    rich = prop.get("title") or []
    return "".join((item.get("plain_text") or "") for item in rich)


def _url_value(prop: dict | None) -> list[str]:
    if not prop:
        return []
    url = prop.get("url")
    return [url] if url else []


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
