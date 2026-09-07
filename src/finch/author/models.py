"""作者发布意图数据模型（P0）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class PublicationIntent(BaseModel):
    """批准时保存的「期待发布」记录（幂等键 source_id = draft.id）。"""

    source_type: Literal["draft"]
    source_id: str
    approved_body: str
    content_hash: str
    approved_at: datetime
    expected_kind: Literal["original"]
