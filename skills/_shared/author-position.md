# 作者立场（Author Position）

## proposed vs confirmed

- **`proposed`**：自动生成（Skill/LLM 产出）的默认状态。表示「这是一个建议立场，未获授权」。
- **`confirmed`**：只有一条路径可达——用户明确输入/确认（`IdeaService.confirm_position` / `finch ideas confirm <id>`）。
- 任何自动流程不得自行置 `confirmed`；LLM 输出的 `USER_CONFIRMED`/`confirmed` 由 Python 强制降级。

## 不改变作者立场

- Skill/领域服务不得在无人确认的情况下改写 `claim`/`decision`/`tradeoff`。
- `confirm_position` 只把 `status` 从 `proposed` 翻到 `confirmed`，不改内容。
- **scoping-only 允许**：缩小适用范围 / 绝对结论改条件结论是允许的表达调整，不改变立场方向；
  推翻或反向改写 decision/tradeoff 仍须重新走确认流程。

## revise 只改表达，不改立场

- `revise_position` 更新立场内容但**不改变状态**（`proposed` 仍 `proposed`，`confirmed` 仍 `confirmed`）。
- 修稿（revise draft）只改措辞/表达，不推翻已确认的立场；要推翻须重新走确认流程。

## topic-dialogue 与练习上下文

- `topic-dialogue` 的 `current_judgment` 是会话内练习上下文，不是已确认的 `AuthorIdea`。
- 不得把 AI 在讨论中提出的论点、反例或推测写入用户确认立场。
- 只有用户明确说出或明确认可的判断，经「保存为观点候选」进入 `idea-discovery`（仍为 `proposed`），再经 `finch ideas confirm` 后才算确认。
- 完成讨论 ≠ 立场已确认；不得因讨论结束而自动 `confirm`。

