---
name: weekly-reflection
description: >
  把一周的关系、表达、讨论和结果转成下一周的重点（LLM 定性复盘）。输入关系质量指标
  （有意义互动 / 继续对话 / 重复同行 / 协作信号 / 待跟进对话）、对话线索、Critic 报告、
  VoiceProfile 变化与发布后反馈；只回答五个问题并给出一个下周训练重点。用于「帮我做本周
  复盘」「这周该练什么表达」类请求。
---

# weekly-reflection

把一周转成下一周一个训练重点。只回答五个问题：

1. 本周与谁形成了真正的来回交流？
2. 哪些互动只有表面反馈？
3. 哪些对话形成了新的观点或实验？
4. 下周应该继续哪三段关系？
5. 哪些表达越来越像自己？

## 执行

`finch weekly [--json]`（关系质量指标与周报指标由代码算，解读由 LLM 做）。

## 边界

- 不自动更新 VoiceProfile（输出 voice_update_candidate，由用户确认后走 `voice-profile`）。
- 不输出十几条泛泛建议；每周只选一个训练重点。
- 不以发帖数量、点赞量、曝光、粉丝变化为主要目标——这些只作辅助数据，不进北极星。

## 参考

- `references/reflection-contract.md` — 输出 schema 与五问。
