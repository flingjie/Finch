---
name: peer-discovery
description: >
  从公开讨论（Twitter/X 搜索）中发现值得长期交流的同行，生成 PeerProfile 候选。
  判断一条帖子背后的人是否值得持续交流——是否与用户兴趣重叠、有实践深度、有可贡献空间、
  有延续潜力；新闻 / 融资 / 纯推广 / 最近已表达的内容跳过。用于「帮我看看这个话题里
  有哪些人值得长期关注/交流」类请求。
---

# peer-discovery

从公开讨论中发现值得长期交流的同行。职责单一：判断一组帖子背后的人是否值得持续交流
（主题重叠、实践深度、可贡献空间、延续潜力），有就产出一个 `PeerProfile` 候选；没有就
产出空列表。它把「哪些帖子值得回」升级为「哪些人值得持续交流」。

本 Skill 只调用 Finch CLI（`finch connect daily` / `finch peers list/show`），不复制业务
逻辑、不直接改数据库。关系评分（`peer_value` 的确定性六维）由 Python 领域服务计算，
本 Skill 只负责语义判断与理由。

## 产出契约（PeerProfile 候选）

- `platform_identities` / `display_name`：作者身份（按 platform + author_id 幂等归一化）。
- `expertise_topics` / `current_interests` / `shared_topics`：主题重叠。
- `why_relevant`：为什么值得继续交流。
- `next_context`：下一步交流的上下文。

## 边界

- 不替用户形成观点（→ `idea-discovery` 才把对话/机会转成观点）。
- 不把别人的经历写成用户经历。
- 不直接生成完整回复（→ `interaction-preparation` / `idea-to-draft`）。
- 不因为帖子热门就推荐；优先真实问题、分歧、失败案例、未解决机制。
- 外部帖子只是信号，不是个人证据。

## 参考

- `references/opportunity-signals.md` — 真实问题 / 分歧 / 失败案例 vs 新闻 / 融资 / 情绪的判据。
- `references/audience-profile.md` — 什么样的交流对象值得投入。
