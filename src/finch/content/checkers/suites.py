"""内容类型自适应检查器套件（规范 §7.2/§7.3）。

按 ``ContentType`` 选择 Critic 检查器组合，替代旧的 ``default_checker_suite`` /
``idea_checker_suite``：去掉强制绑定个人项目的 ``PortabilityChecker`` 与强制
decision/tradeoff 的 ``DecisionChecker``，改为类型专属检查；安全 hard-fail
（``SafetyChecker``）、作者腔调（``VoiceChecker``）与具体性（``SpecificityChecker``）
对所有类型恒开。
"""

from finch.content.checkers.base import Checker
from finch.content.checkers.method_card import MethodCardChecker
from finch.content.checkers.responsiveness import ResponsivenessChecker
from finch.content.checkers.safety import SafetyChecker
from finch.content.checkers.specificity import SpecificityChecker
from finch.content.checkers.structure import StructureChecker
from finch.content.checkers.voice import VoiceChecker
from finch.content.jobs import ContentJob
from finch.content.models import ContentType, RecommendedFormat
from finch.content.voice import VoiceProfile
from finch.llm.base import StructuredInferenceRunner

# 回应型内容 → discussion_reply（与 ResponsivenessChecker 的判定一致）。
_RESPONSE_FORMATS = frozenset({
    RecommendedFormat.REPLY,
    RecommendedFormat.QUOTE,
    RecommendedFormat.DM,
})


def content_type_for(job: ContentJob) -> ContentType:
    """从 ContentJob 推导内容类型：显式字段优先，否则按形式 + intent 推导。

    - reply / quote / dm → discussion_reply；
    - intent=exploration → exploration_hypothesis；
    - 其余 → concept_explanation（method_card / experience_retrospective 须显式指定）。
    """
    if job.content_type is not None:
        return job.content_type
    if job.recommended_format in _RESPONSE_FORMATS:
        return ContentType.DISCUSSION_REPLY
    if job.intent == "exploration":
        return ContentType.EXPLORATION_HYPOTHESIS
    return ContentType.CONCEPT_EXPLANATION


def checker_suite_for(
    content_type: ContentType,
    runner: StructuredInferenceRunner | None = None,
    voice_profile: VoiceProfile | None = None,
) -> list[Checker]:
    """按内容类型选择检查器组合（顺序即执行顺序）。

    所有类型恒开：SafetyChecker（hard-fail 安全门禁）、VoiceChecker（作者腔调）、
    SpecificityChecker（具体性）。类型专属：
    - concept_explanation / experience_retrospective / exploration_hypothesis → StructureChecker；
    - method_card → MethodCardChecker（输入 / 步骤 / 输出 / 限制完整）；
    - discussion_reply → ResponsivenessChecker（为对方留接话空间）；
    - judgment_shift → StructureChecker（判断边界调整需要结构完整性）。
    """
    profile = voice_profile if voice_profile is not None else VoiceProfile()
    common: list[Checker] = [
        SafetyChecker(runner),
        VoiceChecker(runner, profile),
        SpecificityChecker(runner),
    ]
    type_specific: dict[ContentType, list[Checker]] = {
        ContentType.CONCEPT_EXPLANATION: [StructureChecker(runner)],
        ContentType.METHOD_CARD: [MethodCardChecker()],
        ContentType.EXPERIENCE_RETROSPECTIVE: [StructureChecker(runner)],
        ContentType.DISCUSSION_REPLY: [ResponsivenessChecker()],
        ContentType.EXPLORATION_HYPOTHESIS: [StructureChecker(runner)],
        ContentType.JUDGMENT_SHIFT: [StructureChecker(runner)],
    }
    return common + type_specific[content_type]
