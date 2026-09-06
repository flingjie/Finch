"""opencli JSON → AuthorPost 解码（P0；真实 schema 由 contract test 回填）。"""

from datetime import UTC, datetime
from typing import Literal

from finch.author.models import AuthorPost


def decode_author_post(platform: str, author_account_id: str, raw: dict) -> AuthorPost:
    published_at = datetime.now(UTC)  # 占位：由 created_at 解析（见 contract test）
    kind: Literal["original", "reply", "quote"] = "original"
    if raw.get("in_reply_to"):
        kind = "reply"
    elif raw.get("quoted") or raw.get("quoted_tweet"):
        kind = "quote"
    quoted = raw.get("quoted_tweet")
    if isinstance(quoted, dict):
        quoted_post_id = quoted.get("id")
    else:
        quoted_post_id = raw.get("quoted_post_id")
    return AuthorPost(
        platform=platform,
        remote_post_id=str(raw.get("id") or raw.get("tweet_id") or ""),
        author_account_id=author_account_id,
        kind=kind,
        body=str(raw.get("text") or ""),
        url=str(raw.get("url") or ""),
        published_at=published_at,
        replied_to_post_id=raw.get("in_reply_to"),
        quoted_post_id=quoted_post_id,
        likes=_int_or_none(raw.get("likes")),
        replies=_int_or_none(raw.get("replies")),
        reposts=_int_or_none(raw.get("reposts")),
        views=_int_or_none(raw.get("views")),
    )


def _int_or_none(v) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
