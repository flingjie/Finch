"""机会三问统一渲染：他解决什么 / 与你什么相关 / 你能贡献什么（纯呈现层）。"""

from finch.opportunities.models import Opportunity


def render_opportunity_questions(opp: Opportunity) -> str:
    """把机会渲染成三问块；字段缺失时回退到旧自由文本，不丢信息。"""
    lines: list[str] = []

    if opp.problem is not None:
        status = "作者明说" if opp.problem.evidence_status == "author_stated" else "推测"
        lines.append(f"他解决什么：{opp.problem.statement}（{status}）")
    elif opp.topic:
        lines.append(f"他解决什么：{opp.topic}")
    else:
        lines.append("他解决什么：（未给出）")

    if opp.fit is not None:
        refs: list[str] = []
        if opp.fit.practice_refs:
            refs.append("实践 " + "、".join(opp.fit.practice_refs))
        if opp.fit.problem_refs:
            refs.append("问题 " + "、".join(opp.fit.problem_refs))
        suffix = f"（{'；'.join(refs)}）" if refs else ""
        lines.append(f"与你什么相关：{opp.fit.reason}{suffix}")
    elif opp.why_me:
        lines.append(f"与你什么相关：{opp.why_me}")
    else:
        lines.append("与你什么相关：（未给出）")

    if opp.proposal is not None and opp.proposal.contribution:
        p = opp.proposal
        expected = f"，产出 {p.expected_output}" if p.expected_output else ""
        tail = f"（{p.form.value}{expected}）"
        lines.append(f"你能贡献什么：{p.contribution}{tail}")
    else:
        lines.append("你能贡献什么：（待准备）")

    return "\n".join(lines)
