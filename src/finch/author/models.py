"""作者账号 + 发帖同步 + 发布关联（P0）数据模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class AuthorAccount(BaseModel):
    """已验证的作者账号；匹配用稳定 user_id，handle 仅展示。"""

    platform: str
    user_id: str
    handle: str
    verified_at: datetime


class AuthorPost(BaseModel):
    """作者自己发布的一条帖子（幂等键 platform:remote_post_id）。"""

    platform: str
    remote_post_id: str
    author_account_id: str
    kind: Literal["original", "reply", "quote"]
    body: str
    url: str
    published_at: datetime
    replied_to_post_id: str | None = None
    quoted_post_id: str | None = None
    likes: int | None = None
    replies: int | None = None
    reposts: int | None = None
    views: int | None = None


class PublicationIntent(BaseModel):
    """批准时保存的「期待发布」记录（幂等键 source_id = draft.id）。"""

    source_type: Literal["draft"]
    source_id: str
    approved_body: str
    content_hash: str
    approved_at: datetime
    expected_kind: Literal["original"]


class PublicationLink(BaseModel):
    """确定性匹配结果（幂等键 source_id）。"""

    source_id: str
    remote_post_id: str
    matched_by: Literal["exact_text", "similar_text"]
    confidence: float
    linked_at: datetime


class AuthorSyncCursor(BaseModel):
    """同步水位（幂等键 platform:author_account_id）。"""

    platform: str
    author_account_id: str
    last_seen: datetime
