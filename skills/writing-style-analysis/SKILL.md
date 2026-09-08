---
name: writing-style-analysis
description: >
  分析一段文本或链接内容的可观察写作特点，包括开头、结构、节奏、用词、立场、
  具体性和读者关系，并提炼可借鉴但不复制的表达方法。用于“分析这篇文章的风格”
  “这段文字有什么特点”“我可以从这个作者身上学什么”等请求。只生成 StyleReport，
  不判断是否由 AI 创作，不直接改写文本或更新 VoiceProfile。
---

# writing-style-analysis

观察并解释一段文字是怎么写的。只产出 StyleReport，不重写、不做 AI 检测、不自动改画像。

## 执行

`finch style analyze --text/--file/--url [--compare-voice] [--json]`

## 边界

- 不判断是否 AI 创作；不推断作者性格；不模仿/复制他人标志性句子。
- 不自动修改 VoiceProfile（→ 用户认可后走 `voice-profile`）。
- 不评价观点正确性；不重写原文。

## 学习闭环

分析 → 选一个可实验方法 → `expression-practice` 练习 → 认可后 `voice-profile` 人工更新。

## 参考

- `references/analysis-dimensions.md` — 7 个分析维度判据。
- `references/output-contract.md` — StyleReport 契约。
