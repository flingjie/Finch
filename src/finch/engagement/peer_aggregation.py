"""同行聚合：把搜索到的外部帖子按作者（``platform + author_id``）聚合为同行。

这是连接优先改造 Phase 2 的第一步——先回答「这些帖子背后是哪些人」，再做关系评分与
互动机会评分。
"""

from dataclasses import dataclass, field

from finch.peers.models import PeerProfile
from finch.peers.service import PeerService

from .models import ExternalPost


@dataclass
class PeerBundle:
    """一个同行的聚合视图：PeerProfile + 该同行本轮的全部帖子。"""

    profile: PeerProfile
    posts: list[ExternalPost] = field(default_factory=list)


def aggregate_by_peer(
    posts: list[ExternalPost],
    *,
    peer_service: PeerService | None = None,
) -> list[PeerBundle]:
    """按 ``(platform, author_id)`` 聚合帖子；同一作者跨多条帖子只产生一个 PeerBundle。

    保持首次出现顺序（按帖子顺序），peer id 由 ``PeerService`` 的幂等派生决定。
    """
    svc = peer_service or PeerService()
    by_id: dict[str, PeerBundle] = {}
    for post in posts:
        profile = svc.from_author(
            platform=post.platform,
            author_id=post.author_id,
            username=post.author_name,
        )
        bundle = by_id.get(profile.id)
        if bundle is None:
            bundle = PeerBundle(profile=profile)
            by_id[profile.id] = bundle
        bundle.posts.append(post)
    return list(by_id.values())
