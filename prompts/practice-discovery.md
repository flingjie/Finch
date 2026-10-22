# 实践发现（外部来源）

围绕用户的一个具体问题，从候选来源内容中找出**值得尝试的方法/结论**，最多 3 条。

## 用户问题
标题：{title}
为什么重要：{why_it_matters}
预期变化：{desired_outcome}
约束：{constraints}

## 候选来源（每条带 `[ref]` 引用）
{candidates}

## 规则

- 只从上面候选内容里提炼，不得编造来源；`evidence_refs` 只能填上面出现过的 ref。
- 每条说明：为什么与问题相关（why_relevant）、证据是什么（evidence_refs + excerpt）、
  适用条件（applicability）、最小可执行动作（smallest_action）。
- 与问题无关的候选不要输出；不足 3 条就返回更少，不要凑数。
- 只返回 JSON，匹配给定 schema。
