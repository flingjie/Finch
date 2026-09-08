---
name: conversation-scout
description: >
  从公开讨论（Twitter/X 搜索）中寻找值得交流的人、问题和切入口，产出「交流机会」
  （Opportunity），由用户决定是否转成自己的 Idea。判断一条帖子是否承载真实问题 / 分歧 /
  失败案例 / 未解决机制；新闻 / 融资 / 纯情绪 / 最近已表达的内容跳过。用于「帮我看看这个话题
  里有什么值得回应/交流」类请求。
---

# conversation-scout

从公开讨论中寻找交流机会。职责单一：判断一条帖子是否承载「值得交流的真实问题 / 分歧 /
失败案例 / 未解决机制」，有就产出**一个** `Opportunity`；没有就产出空列表。

本 Skill 只调用 Finch CLI（`finch scout search/list/show`），不复制业务逻辑、不直接改数据库。

## 产出契约（Opportunity）

- `source_post`：帖子引用（url / author / text）。
- `shared_tension` / `why_relevant` / `response_angles` / `knowledge_gap` / `relationship_value`。

## 边界

- 不替用户形成观点（→ `idea-discovery` 才把机会转成 Idea）。
- 不把别人的经历写成用户经历。
- 不直接生成完整回复（→ `expression-practice` / `idea-to-draft`）。
- 不因为帖子热门就推荐；优先真实问题、分歧、失败案例、未解决机制。

## 参考

- `references/opportunity-signals.md` — 真实问题 / 分歧 / 失败案例 vs 新闻 / 融资 / 情绪的判据。
- `references/audience-profile.md` — 什么样的交流对象值得投入。
