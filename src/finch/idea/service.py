"""idea 服务：idea 草稿的 Critic 套件与定向重写（纯函数，不访问 DB）。

被 ``finch drafts`` 使用；旧的 assess/write 一体式入口已由 Skill 架构
（``finch ideas create`` + ``finch drafts create``）取代，本模块不依赖 finch.cli。
"""

from typing import cast

from finch.content.checkers.base import Checker, CheckResult
from finch.content.critic import default_checker_suite
from finch.content.jobs import ContentJob
from finch.content.models import Draft
from finch.content.voice import VoiceProfile
from finch.content.writer import _render_failed_checks, _render_job_context
from finch.idea.models import RewriteIdeaOutput
from finch.llm.base import StructuredInferenceRunner

_IDEA_REWRITE_PROMPT = """\
You rewrite a draft to address specific critic check failures. Return JSON matching the schema.
Instructions:
- Keep the same voice and personal-judgment framing as the Original draft.
- Fix exactly the failures listed under Failed checks. Do NOT restyle, polish, or improve
  the rest of the draft — change only what is needed to resolve the listed failures.

{job_context}## Original draft
{body}

## Failed checks
{rewrite_instructions}
"""


def rewrite_idea(
    runner: StructuredInferenceRunner,
    draft: Draft,
    failed_checks: list[CheckResult],
    job: ContentJob | None,
) -> Draft:
    """按 Critic 失败项定向重写（只回传新正文，claims 恒为空）。"""
    prompt = _IDEA_REWRITE_PROMPT.format(
        body=draft.body,
        job_context=_render_job_context(job) if job is not None else "",
        rewrite_instructions=_render_failed_checks(failed_checks),
    )
    out = cast(RewriteIdeaOutput, runner.run(prompt, RewriteIdeaOutput))
    return draft.model_copy(update={"body": out.body})


def idea_checker_suite(
    runner: StructuredInferenceRunner | None,
    voice_profile: VoiceProfile | None = None,
) -> list[Checker]:
    """Critic 套件去掉 EvidenceChecker（idea 允许个人判断/假设，不强制证据绑定）。"""
    return [c for c in default_checker_suite(runner, voice_profile) if c.name != "evidence"]
