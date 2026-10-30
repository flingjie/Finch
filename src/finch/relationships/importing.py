"""手工导入公开链接 / 经允许的交流摘要 → 稳定 note_id（source_ref）。

P0 只做「记录原始材料」，不扩展抓取平台：公开链接不自动抓取正文，只把 URL + 可选
粘贴文本存为 ``ImportedNote``，返回可被人物/事件/交流引用的 ``note_id``。
"""

from __future__ import annotations

from finch.relationships.ids import note_id_for
from finch.relationships.models import ImportedNote
from finch.relationships.repository import NoteRepository
from finch.storage.workspace import Workspace


def import_public_link(
    ws: Workspace, url: str, *, title: str = "", text: str = "", author: str = ""
) -> str:
    """记录一条公开链接（不抓取正文），返回可引用的 note_id。"""
    note = ImportedNote(
        note_id=note_id_for(url, text),
        kind="public_link",
        url=url.strip(),
        title=title.strip(),
        text=text.strip(),
        author=author.strip(),
    )
    NoteRepository(ws).upsert(note)
    return note.note_id


def import_summary(ws: Workspace, text: str, *, url: str = "") -> str:
    """记录一条经允许使用的交流摘要，返回可引用的 note_id。"""
    note = ImportedNote(
        note_id=note_id_for(url, text),
        kind="summary",
        url=url.strip(),
        text=text.strip(),
    )
    NoteRepository(ws).upsert(note)
    return note.note_id
