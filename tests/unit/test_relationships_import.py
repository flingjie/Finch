"""关系领域导入单测：公开链接 / 交流摘要 → 稳定 note_id。"""

from __future__ import annotations

from finch.relationships.importing import import_public_link, import_summary
from finch.relationships.repository import NoteRepository
from finch.storage.workspace import Workspace


def test_import_public_link_stores_note(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    note_id = import_public_link(ws, "https://x.com/alice/status/1", text="agent 审批后仍重复执行")
    assert note_id.startswith("note_")
    note = NoteRepository(ws).get(note_id)
    assert note.kind == "public_link"
    assert note.url == "https://x.com/alice/status/1"
    assert note.text == "agent 审批后仍重复执行"


def test_import_summary_stores_note(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    note_id = import_summary(ws, "对方约一周后反馈日志")
    note = NoteRepository(ws).get(note_id)
    assert note.kind == "summary"
    assert note.text == "对方约一周后反馈日志"


def test_import_is_idempotent(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    a = import_public_link(ws, "https://x.com/alice/status/1", text="same")
    b = import_public_link(ws, "https://x.com/alice/status/1", text="same")
    assert a == b
