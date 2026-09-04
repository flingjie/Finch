"""执行前保护（Phase 5 审批与执行保护）。

纯函数 ``evaluate_execution`` 在真正发送前做最后一层前置校验：未批准、草稿被修改、
超过作者/每日限额、帖子已不可用都会阻断；帖子可用性无法确认时返回 UNKNOWN 而非成功。

关键不变量（对应执行计划 5 验收）：
- 未批准的公开表达无法被发送：``status != APPROVED`` 一律 REJECTED；
- 远端返回不确定时状态为 UNKNOWN，绝不标记为成功：``post_available is None`` 绝不返回
  APPROVED。

本轮不实现任何真实网络写操作（Finch Twitter 适配器只读，denylist 阻断 reply/quote 等
写命令）；本模块只是纯前置守卫，调用方据此决定是否发送，并用 ``record_execution`` 记录
结果。
"""

from enum import StrEnum

from ..settings import EngagementSettings
from .models import InteractionCandidate, InteractionStatus


class ExecutionStatus(StrEnum):
    """执行前置校验结果。"""

    APPROVED = "approved"  # 所有前置条件满足
    REJECTED = "rejected"  # 被确定规则阻断
    UNKNOWN = "unknown"  # 某前置条件无法确认
    FAILED = "failed"  # 出现错误


def evaluate_execution(
    candidate: InteractionCandidate,
    *,
    post_available: bool | None,
    draft_unchanged: bool,
    author_today_count: int,
    public_today_count: int,
    engagement: EngagementSettings,
) -> tuple[ExecutionStatus, list[str]]:
    """执行前校验，返回 (结果, 阻断理由列表)。

    ``post_available`` 为 None 表示「无法确认帖子是否仍存在」（远端不确定/超时），此时
    返回 UNKNOWN，绝不返回 APPROVED。规则按顺序短路，返回第一个阻断项的理由：
    1. 未批准 → REJECTED（「未批准的公开表达不可发送」的保证）；
    2. 草稿自批准后被修改 → REJECTED；
    3. 该作者今日互动数达到上限 → REJECTED；
    4. 公开回复今日总数达到上限 → REJECTED；
    5. 帖子已不可用（False）→ REJECTED；
    6. 帖子可用性无法确认（None）→ UNKNOWN；
    7. 全部通过 → APPROVED。
    """
    if candidate.status != InteractionStatus.APPROVED:
        return ExecutionStatus.REJECTED, ["not approved"]
    if not draft_unchanged:
        return ExecutionStatus.REJECTED, ["draft modified since approval"]
    if author_today_count >= engagement.per_author_daily_limit:
        return ExecutionStatus.REJECTED, ["author daily limit reached"]
    if public_today_count >= engagement.max_public_replies:
        return ExecutionStatus.REJECTED, ["public reply limit reached"]
    if post_available is False:
        return ExecutionStatus.REJECTED, ["post no longer available"]
    if post_available is None:
        return ExecutionStatus.UNKNOWN, ["post availability could not be verified"]
    return ExecutionStatus.APPROVED, []
