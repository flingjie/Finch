# 作者立场（Author Position）

## proposed vs confirmed

- **`proposed`**：自动生成（Skill/LLM 产出）的默认状态。表示「这是一个建议立场，未获授权」。
- **`confirmed`**：只有两条路径可达：
  1. 用户明确输入/确认（`IdeaService.confirm_position` / `finch run resolve --confirm`）；
  2. 命中已批准 fingerprint（`position_fingerprint` 与 `PositionApprovalRepository.find_active` 逐字一致且未被撤销、`change_mind_if` 为空）。
- 任何自动流程不得自行置 `confirmed`；LLM 输出的 `USER_CONFIRMED`/`confirmed` 由 Python 强制降级。

## 不改变作者立场

- Skill/领域服务不得在无人确认的情况下改写 `claim`/`decision`/`tradeoff`。
- `confirm_position` 只把 `status` 从 `proposed` 翻到 `confirmed`，不改内容。

## revise 只改表达，不改立场

- `revise_position` 更新立场内容但**不改变状态**（`proposed` 仍 `proposed`，`confirmed` 仍 `confirmed`）。
- 修稿（revise draft）只改措辞/表达，不推翻已确认的立场；要推翻须重新走确认流程。

## 指纹复用边界

- 复用仅在「立场逐字一致 + 未被撤销 + `change_mind_if` 为空」时成立。
- 立场内容变化 → 展示 diff 后重新确认；曾被撤销 → 禁止复用。
