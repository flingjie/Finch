---
name: weekly-reflection
description: >
  把一周的表达、修改、讨论和结果转成下一周一个训练重点（LLM 定性复盘）。输入首稿与最终稿、
  Critic 报告、人工修改记录、VoiceProfile 变化、有意义的回复、ConversationEvidence 与发布
  后效果数据（含确定性指标）；只回答四个问题并给出一个下周训练重点。用于「帮我做本周复盘」
  「这周表达上练什么」类请求。
---

# weekly-reflection

把一周转成下一周一个训练重点。只回答四个问题：本周想清楚了什么 / 哪次表达最像自己 /
哪次交流产生新连接或新问题 / 下周只练哪一个表达问题。

## 执行

`finch weekly [--json]`（指标由代码算，解读由 LLM 做）。

## 边界

- 不自动更新 VoiceProfile（输出 voice_update_candidate，由用户确认后走 `voice-profile`）。
- 不输出十几条泛泛建议；每周只选一个表达实验。
- 不以发帖数量、点赞量为主要目标。

## 参考

- `references/reflection-contract.md` — 输出 schema 与四问。
