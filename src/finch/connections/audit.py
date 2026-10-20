"""数据审计：重复消息、未知方向、孤立互动、未确认身份、未来发生时间。

纯函数、无 IO。供 ``finch connections audit`` 使用；也作为统一事实写入后的数据质量
检查（例如旧 follow-up 幂等键漂移后的重复消息缓解）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from finch.conversations.models import ConversationThread
from finch.engagement.models import InteractionRecord
from finch.peers.models import PeerProfile


@dataclass
class DataAuditReport:
    """五类数据异常。为空列表表示干净。"""

    duplicate_messages: list[dict] = field(default_factory=list)
    unknown_direction: list[str] = field(default_factory=list)
    orphan_interactions: list[str] = field(default_factory=list)
    unconfirmed_identity: list[str] = field(default_factory=list)
    future_occurred_at: list[str] = field(default_factory=list)


def audit_connections(
    *,
    interactions: list[InteractionRecord],
    threads: list[ConversationThread],
    peers: list[PeerProfile],
    now: datetime,
) -> DataAuditReport:
    """审计关系领域数据，返回五类异常。"""
    report = DataAuditReport()
    peer_ids = {p.id for p in peers}
    thread_ids = {t.id for t in threads}
    rec_ids = {r.id for r in interactions}

    # 重复消息：同 (namespace, platform_message_id) 或同 source_url，不同 id。
    seen_msg: dict[tuple[str, str], str] = {}
    seen_url: dict[str, str] = {}
    for r in interactions:
        if r.platform_message_id:
            key = (r.namespace, r.platform_message_id)
            if key in seen_msg and seen_msg[key] != r.id:
                report.duplicate_messages.append(
                    {
                        "a": seen_msg[key],
                        "b": r.id,
                        "kind": "platform_message_id",
                        "value": r.platform_message_id,
                        "namespace": r.namespace,
                    }
                )
            else:
                seen_msg[key] = r.id
        if r.source_url:
            if r.source_url in seen_url and seen_url[r.source_url] != r.id:
                report.duplicate_messages.append(
                    {
                        "a": seen_url[r.source_url],
                        "b": r.id,
                        "kind": "source_url",
                        "value": r.source_url,
                    }
                )
            else:
                seen_url[r.source_url] = r.id

    for r in interactions:
        if r.direction == "unknown":
            report.unknown_direction.append(r.id)
        if r.occurred_at > now:
            report.future_occurred_at.append(r.id)
        if r.thread_id is not None and r.thread_id not in thread_ids:
            report.orphan_interactions.append(r.id)
        if r.peer_id not in peer_ids:
            report.unconfirmed_identity.append(r.id)

    # 线程引用不存在的记录（孤立互动）。
    for t in threads:
        for rid in t.interaction_ids:
            if rid not in rec_ids:
                report.orphan_interactions.append(f"{t.id}:{rid}")

    return report
