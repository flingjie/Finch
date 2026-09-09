"""同行归一化服务：外部作者按 ``platform + author_id`` 幂等归一化为 PeerProfile。"""

import hashlib

from .models import PeerProfile, Platform, PlatformIdentity


def peer_id_for(platform: str, author_id: str) -> str:
    """由 ``(platform, author_id)`` 派生稳定 peer id（幂等：同一作者恒同 id）。"""
    digest = hashlib.sha256(f"{platform}:{author_id}".encode()).hexdigest()
    return f"peer_{digest[:12]}"


def profile_url_for(
    platform: Platform | str,
    *,
    username: str = "",
    author_id: str = "",
) -> str | None:
    """由平台身份构造公开主页 URL（无外部 API；handle 优先 username，否则 author_id）。"""
    handle = (username or author_id).strip().lstrip("@")
    if not handle:
        return None
    if platform == "x":
        return f"https://x.com/{handle}"
    if platform == "reddit":
        return f"https://www.reddit.com/user/{handle}"
    return None


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
            platform=platform,
            author_id=author_id,
            username=username,
            url=url or profile_url_for(platform, username=username, author_id=author_id),
        )
        return PeerProfile(
            id=peer_id_for(platform, author_id),
            platform_identities=[identity],
            display_name=username or author_id,
        )

    def merge_identity(
        self, profile: PeerProfile, identity: PlatformIdentity
    ) -> PeerProfile:
        """把身份并入 profile；同 ``(platform, author_id)`` 幂等（不重复添加），返回新对象。

        已存在同键身份时：若旧身份缺 ``url`` 而新身份有，则补上 url，不丢其他字段。
        """
        for idx, existing in enumerate(profile.platform_identities):
            if (existing.platform, existing.author_id) != (identity.platform, identity.author_id):
                continue
            if existing.url or not identity.url:
                return profile
            updated = profile.model_copy(deep=True)
            updated.platform_identities[idx] = existing.model_copy(update={"url": identity.url})
            return updated
        updated = profile.model_copy(deep=True)
        updated.platform_identities.append(identity)
        return updated

    def merge_discovered(
        self, existing: PeerProfile | None, discovered: PeerProfile
    ) -> PeerProfile:
        """发现落库：无既有档案用发现骨架；有则并入新平台身份与 source_refs，保留关系字段。

        防止 ``_persist_discovery`` 每次用 ``from_author`` 骨架整对象覆写已积累的
        ``relationship_stage`` / ``why_relevant`` / ``next_context`` 等字段。
        """
        if existing is None:
            return discovered
        merged = existing
        for identity in discovered.platform_identities:
            merged = self.merge_identity(merged, identity)
        if discovered.source_refs:
            seen = set(merged.source_refs)
            extra = [ref for ref in discovered.source_refs if ref not in seen]
            if extra:
                merged = merged.model_copy(
                    update={"source_refs": [*merged.source_refs, *extra]}
                )
        return merged
