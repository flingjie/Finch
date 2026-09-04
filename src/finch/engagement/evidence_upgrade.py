"""conversation → personal 证据升级（执行计划 Phase 6 反馈回流与证据升级）。

职责：
1. ``extract_conversation_evidence``：从一次互动的讨论串中提取问题 / 分歧 / 可验证假设 /
   可能的实验，保存为 ``conversation`` 类型候选证据（单次 LLM 调用，空讨论直接短路）。
2. ``promote_to_personal``：纯函数、确定性升级门 —— 仅当 conversation 证据已被验证
   （``verified=True``）且验证来源属于 case/code/experiment/multi_source 时才生成 personal
   证据卡；否则返回 None，调用方不得写入 EvidenceRepository。

关键不变量（执行计划 2 范围边界 + 4 证据升级规则）：
- 外部帖子（external）绝不直接成为个人证据：``promote_to_personal`` 只接受
  ``ConversationEvidence``（``origin`` 被类型系统锁定为 ``"conversation"``），不存在把
  ``ExternalPost`` 提升为 personal 证据的代码路径；产出的 EvidenceCard 以
  ``event_id=interaction_id`` 回溯到产生它的互动，而非外部帖子本身。
- conversation → personal 必须经过验证：``verified is False`` 或验证种类不在白名单时
  一律返回 None，调用方无法绕过。
"""

from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from ..codex.runner import CodexRunner
from ..evidence.models import ClaimConfidence, EvidenceCard
from .models import ConversationEvidence, Verification

_PROMPT_PATH = Path("prompts/extract-conversation-evidence.md")

# 允许 conversation → personal 升级的验证来源（执行计划 4：案例/代码/实验/多来源）。
_PROMOTABLE_VERIFICATION_KINDS: frozenset[str] = frozenset(
    {"case", "code", "experiment", "multi_source"}
)


class ExtractedEvidenceItem(BaseModel):
    """模型从讨论串中提取的单条 conversation 证据（仅 kind + statement）。"""

    kind: Literal["question", "disagreement", "hypothesis", "experiment"]
    statement: str


class EvidenceBatchOutput(BaseModel):
    """一次讨论提取返回的证据列表。"""

    items: list[ExtractedEvidenceItem] = Field(default_factory=list)


def extract_conversation_evidence(
    runner: CodexRunner,
    interaction_id: str,
    post_id: str,
    discussion: str,
) -> list[ConversationEvidence]:
    """从一次互动的讨论串中提取问题 / 分歧 / 假设 / 实验（conversation 候选证据）。

    - 空或纯空白讨论 → 空输出、0 次 LLM 调用（与 ``score_posts``/``generate_proposals``
      一致的空输入短路）。
    - 模型只负责 ``kind`` + ``statement``；``id`` / ``interaction_id`` / ``post_id`` /
      ``origin`` / ``verified`` 全部由本函数确定性填充，避免模型伪造溯源或绕过验证。
    - ``id`` 形如 ``ce_<interaction_id>_<i>``，使仓储 upsert 幂等。
    """
    if not discussion.strip():
        return []
    prompt = _PROMPT_PATH.read_text().format(discussion=discussion)
    output = cast(EvidenceBatchOutput, runner.run(prompt, EvidenceBatchOutput))
    return [
        ConversationEvidence(
            id=f"ce_{interaction_id}_{idx}",
            interaction_id=interaction_id,
            post_id=post_id,
            origin="conversation",
            kind=item.kind,
            statement=item.statement,
            verified=False,
        )
        for idx, item in enumerate(output.items)
    ]


def promote_to_personal(
    evidence: ConversationEvidence,
    verification: Verification,
) -> EvidenceCard | None:
    """conversation → personal 升级门（纯函数、确定性，不写任何存储）。

    仅当 ``evidence.verified is True`` 且 ``verification.kind`` 属于 case / code /
    experiment / multi_source 时返回 EvidenceCard；否则返回 None。返回的卡片：

    - ``event_id = evidence.interaction_id``：个人证据回溯到产生它的互动（而非外部帖子本身）；
    - ``claim = evidence.statement``：个人新增判断的原文；
    - ``confidence = SUPPORTED``：已经过案例/代码/实验/多来源验证，但并非 repo 代码直接
      证明的 VERIFIED，也非仅用户口述的 USER_CONFIRMED；
    - ``sources = []``、``topics = []``、``publishable = False``：本函数不自动写入
      EvidenceRepository，调用方负责补充来源、决定可发布性并 upsert，保证「外部帖子
      不直接成为个人证据」这一不变量不被绕过。
    """
    if not evidence.verified:
        return None
    if verification.kind not in _PROMOTABLE_VERIFICATION_KINDS:
        return None
    return EvidenceCard(
        id=f"ev_{evidence.id}",
        event_id=evidence.interaction_id,
        claim=evidence.statement,
        sources=[],
        confidence=ClaimConfidence.SUPPORTED,
        publishable=False,
        topics=[],
    )
