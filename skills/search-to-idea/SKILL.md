---
name: search-to-idea
description: >
  从公开讨论（Twitter/X 搜索）中提炼可发布的工程 Idea（IdeaCandidate）。判断一条帖子是
  否承载「读者值得知道」的真实问题 / 反例 / 工程缺口，是就产出一个候选；新闻 / 融资 /
  纯情绪 / 最近已表达的内容跳过（空列表）。用于「把某个话题的公开讨论变成可写的内容想法」
  类请求。
---

# search-to-idea

从公开讨论中提炼 Idea。职责单一：判断一条帖子是否承载「读者值得知道的真实问题 / 反例 / 工程缺口」，有就产出**一个** `IdeaCandidate`，没有就产出空列表。

本 Skill 只调用 Finch CLI（`finch ideas search`），不复制业务逻辑、不直接改数据库、不猜测状态。

## 职责

- 输入：一组已规范化的公开帖子（由 `finch ideas search` 搜索并预处理）。
- 输出：`IdeaCandidate` 列表（契约见 `_shared/idea-contract.md`）。
- 真实问题 / 反例 / 工程缺口 → 一个 `IdeaCandidate`。
- 新闻 / 融资 / 纯情绪 → 空列表。
- 最近已表达（重复）内容 → 去重（空列表）。
- 外部作者亲历 → 保持外部来源，不写成作者本人亲历。

## 执行

用 `finch ideas search [--topic TOPIC] [--json]`：

1. 用 `QueryBuilder` + `OpenCliClient.search` 搜索话题并召回帖子。
2. `normalize_tweets` 去重 + 过滤噪音（空文本 / 广告 / 被屏蔽作者）。
3. `SearchService.to_ideas` 做机会提炼（真实问题 / 反例 / 缺口判定 + 去重 + 外部亲历中性化）。
4. `IdeaService.create_candidate` 幂等落库为 `ContentJob`（PROPOSED）。

## 产出契约（`IdeaCandidate`）

见 `_shared/idea-contract.md`，要点：

- `core_point` 只有一个中心主张；多个主张拆成多个候选。
- `source_refs` 可追溯（`type="post"` + 帖子 URL + 一句话摘要，能反查到具体帖子）。
- `author_position.status` 一律 `proposed`（自动生成，未获授权）。
- 外部信号未经作者一手验证 → `boundaries.known` 为空、`inferred` 承载中性化信号，传递到 Draft 校验。
- `origin="search"`；`generator.skill="search-to-idea"`。

## 强制规则

- 证据优先：外部帖子只是信号，不是个人证据（见 `_shared/evidence-policy.md`）。
- **外部亲历不写成作者亲历**：进入 `core_point` / `reader_problem` / `boundaries` 的表述必须中性化（去第一人称）；原文只保留在 `source_refs.summary`。
- 自动生成立场一律 `proposed`；只有用户确认才是 `confirmed`（见 `_shared/author-position.md`）。
- 不自动发布；分数由代码算，LLM 输出不携带 total（见 `_shared/quality-policy.md`）。

## 判断一条帖子是否可提炼 Idea

见 `references/opportunity-signals.md`。

## 参考

- `references/opportunity-signals.md` — 真实问题 / 反例 / 缺口 vs 新闻 / 融资 / 情绪的判据。
- `_shared/idea-contract.md` — IdeaCandidate 契约。
- `_shared/evidence-policy.md` — 证据优先与外部帖 ≠ 个人证据。
- `_shared/author-position.md` — proposed vs confirmed。
- `_shared/quality-policy.md` — 不自动发布、分数由代码算。
