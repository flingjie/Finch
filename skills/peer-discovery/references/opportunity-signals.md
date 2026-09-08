# 机会信号：真实问题 vs 新闻 / 融资 / 情绪

从公开讨论帖子判断是否可提炼一个可发布的工程 Idea（真实问题 / 反例 / 工程缺口），用于决定产出一个 `IdeaCandidate` 还是空列表。

## 机会信号（可提炼）

- **真实问题**：作者在公开讨论中报告具体技术坑（"doesn't work"、"broken"、"bug"、"fails"、"crash"、"race condition"、"太慢"、"崩溃"、"踩坑"）。
- **反例**：作者给出与主流叙事相悖的实测（"in practice"、"turns out"、"实际上"、"翻车"）。
- **工程缺口**：作者指出缺少的工具 / 能力 / 覆盖（"missing"、"gap"、"no one"、"缺口"、"缺"）。

判据：存在一个「别人可能也遇到、并能从讨论中学到东西」的真实问题或缺口。

## 非机会信号（跳过 → 空列表）

- **新闻**：产品发布 / 版本上线（"announces"、"launches"、"releases"、"new version"、"发布"、"上线"）。
- **融资**：融资 / 估值 / 收购（"raised"、"funding"、"series A/B"、"valuation"、"acquires"、"融资"、"估值"）。
- **纯情绪**：空洞表态（"amazing"、"awesome"、"incredible"、"wow"）。

判据：没有「读者值得知道」的新信息，只是消息或情绪。

## 优先级

机会信号优先于噪音信号：一条帖子既提到融资又报告了具体 bug，按**机会**处理（真实问题 > 新闻/融资/情绪）。

## 外部亲历不写成作者亲历

外部作者的第一人称经历（"I spent 3 weeks debugging…"、"我踩了…"）不得被采纳为作者本人亲历：

- 进入作者内容字段（`core_point` / `reader_problem` / `boundaries`）的表述必须**中性化**（去掉第一人称代词）。
- 原文只保留在 `source_refs.summary`（来源摘要，可追溯到帖子 URL）。
- 外部信号未经作者一手验证 → `boundaries.known` 为空，信号归入 `inferred`。

## 最近已表达 → 去重

同一条已表达内容（规范化后相同）只产出一次；重复帖子跳过。跨轮次由 `IdeaService.create_candidate` 的 `generation_key` 幂等去重兜底。

## 立场一律 proposed

search 自动生成的立场是「建议」，不是授权结论。只有用户显式确认才是 `confirmed`（见 `_shared/author-position.md`）。
