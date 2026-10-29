---
name: product-feedback-research
description: >
  对一个已发现的产品做真实用户反馈研究：定向搜索 Reddit / X / GitHub Issues / PR /
  Discussions 上用户讨论，过滤归属与宣传，抽取结构化反馈，综合成带来源的 Feedback
  Report，并把结果接入 Solution Explorer。用于「这个产品用户实际怎么用」「用户为什么
  抱怨/放弃」「用户的痛点是什么」类请求。用户反馈是证据，不是投票；不编造反馈。
---

# product-feedback-research

研究一个产品的**真实用户反馈**：用户如何使用、评价、抱怨、请求什么，以及从反馈中能发现
什么改进机会、值得向创业者提出什么问题。与创业者自述（产品卡片）分开，是 Solution
Explorer 的输入之一。

确定性 Python 负责搜索、归属过滤、去重、证据门禁与缓存；LLM 只做语义抽取与综合。本 Skill 只调 CLI 并呈现。

## CLI

```bash
uv run finch products feedback <product_id> [--refresh] [--json]
uv run finch products explore <product_id> [--refresh] [--skip-feedback] [--json]
```

- `products feedback`：研究用户反馈，输出带来源的 Feedback Report。
- `--refresh`：强制重新搜索（默认幂等复用缓存，TTL 见 `products.feedback.stale_hours`）。
- `products explore`：默认在无反馈或反馈过期时自动跑反馈研究，再纳入方案分析。
- `--skip-feedback`：仅基于已有产品资料分析，不研究用户反馈。

## 研究过程（五步）

1. **搜索**：按产品名/别名构造 Reddit / X 定向查询；GitHub 直接抓产品仓库 issue / PR / discussion。
2. **过滤**：归属（产品名/key/域名）、去宣传（创业者自述/营销）、去重（跨平台转发只算一条）。
3. **提取**：LLM 从候选讨论抽取结构化反馈（场景 / 问题 / 期望 / 摘录 / 类型 / 第一手层级）。
4. **核验**：`original_excerpt` 必须逐字来自候选文本；`source_url` 必须命中候选链接；无引用标 `assumption`。
5. **综合**：聚合高频痛点、未满足需求、意见矛盾、可问创业者的问题；无足够反馈返回 `insufficient_feedback`。

## 向用户呈现

- 先给结论：反馈条数 / 是否证据不足 / 来源覆盖；再逐条给摘录 + 可点击链接。
- 高频痛点、未满足需求、矛盾、可问问题各一段，每条尽量可追溯到来源。
- `insufficient_feedback`：明确说「证据不足」，不给「用户很少」或「市场小」的结论。
- 区分第一手使用（first_hand）与转述（second_hand/unknown）；第一手才可谈「可观察的收益」。
- 不因评论数量或情绪打分替代产品判断；不输出任何总分/评分。

## 边界

- 只读：`gh` / `opencli` 只读，不外发、不评论、不私信。
- 证据≠投票：样本有偏（愿意公开评论的用户不代表全部用户），不做市场大小推断。
- 同名产品歧义：先做归属过滤 + LLM `product_match` 确认，再谈反馈。
- 宣传≠反馈：创业者自述、营销内容不算用户反馈。
- 无证据即停：候选不足时不无限搜索、不反复调用模型。
