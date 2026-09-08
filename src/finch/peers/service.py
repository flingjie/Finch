"""同行归一化服务：外部作者按 ``platform + author_id`` 幂等归一化为 PeerProfile。"""

import hashlib

from .models import PeerProfile, Platform, PlatformIdentity


def peer_id_for(platform: str, author_id: str) -> str:
    """由 ``(platform, author_id)`` 派生稳定 peer id（幂等：同一作者恒同 id）。"""
    digest = hashlib.sha256(f"{platform}:{author_id}".encode()).hexdigest()
    return f"peer_{digest[:12]}"


class PeerService:
    """把外部作者归一化为 PeerProfile；同一作者跨多条帖子只产生一个平台身份。

    归一化只做确定性去重与稳定 id 派生；跨平台「同一自然人」的关联不在本服务范围
    （无共享 handle/邮箱契约），由上层在有人工信号时补 source_refs 或共享主题。
    """

    def from_author(
        self,
        *,
        platform: Platform,
        author_id: str,
        username: str = "",
        url: str | None = None,
    ) -> PeerProfile:
        """从单平台作者首次发现创建一个 DISCOVERED 阶段 PeerProfile。"""
        identity = PlatformIdentity(
            platform=platform, author_id=author_id, username=username, url=url
        )
        return PeerProfile(
            id=peer_id_for(platform, author_id),
            platform_identities=[identity],
            display_name=username or author_id,
        )

    def merge_identity(
        self, profile: PeerProfile, identity: PlatformIdentity
    ) -> PeerProfile:
        """把身份并入 profile；同 ``(platform, author_id)`` 幂等（不重复添加），返回新对象。"""
        keys = {(i.platform, i.author_id) for i in profile.platform_identities}
        if (identity.platform, identity.author_id) in keys:
            return profile
        updated = profile.model_copy(deep=True)
        updated.platform_identities.append(identity)
        return updated
