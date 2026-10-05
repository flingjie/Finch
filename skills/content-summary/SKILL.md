---
name: content-summary
description: >
  读一篇帖子/文章，产出忠实、完整、简洁的内容摘要：一句话主旨、核心要点、
  关键依据或例子（标注来源）、条件与限制。用于「这篇帖子主要讲了什么」
  「帮我快速读懂」「提取要点」「summarize this post」等请求。只做内容摘要，
  不做写法点评（→ article_analysis）、不改写（→ finch drafts revise）、
  不判断观点对错、不重铸信息（→ sticky-message）。
---

# content-summary

读懂一篇帖子到底说了什么：一句话主旨 → 核心要点 → 关键依据或例子 → 条件与限制。
只产出摘要，不改写、不做写法点评、不判断对错、不自动进入任何表达流水线。

## 执行

`finch summarize --text/--file/--url [--json] [--no-save]`

摘要默认落库 Workspace（`content_summaries`）；`--no-save` 可跳过写入。呈现含摘要 `id`。
落库是为 reply-crafting / idea-discovery 等流程按 id 复用留的接缝，v1 无回看命令。

四项产物：
1. **一句话主旨**：作者最想传达什么。
2. **核心要点**：按内容长度提取，短帖不硬凑数量。
3. **关键依据或例子**：保留支撑观点的重要信息，标注来源（作者 / 引用 / 回复 / 未标明）。
4. **条件与限制**：保留原文的「可能」「仅适用于」等限定；缺少上下文时明确说明。

## 质量标准

**忠实、完整、简洁**。作者观点标为作者观点；回复与引用内容区分来源；
提取原文时不额外扩写「对我的启发」；不做写法点评（那是 `article_analysis` 的职责）。

## 边界

- 不判断观点对错；不推断作者性格；不判断是否 AI 创作。
- 不重写原文；不生成分享稿；不重铸信息（→ `sticky-message`）。
- 表达方法/写作风格分析 → `finch article analyze`。

## 参考

- `references/output-contract.md` — ContentSummary 契约（确定性字段 + 四内容字段 + 来源标注）。
