---
name: collision-lab
description: >
  识别跨领域结构相似碰撞，区分表面类比与结构类比，输出 CollisionCard
  （shared_question / transferable_mechanism / falsifiable_hypothesis）。
  每周只深度处理一个最值得验证的碰撞。
---

# collision-lab

输入：两个领域的证据与人物上下文。
输出：CollisionCard（至少两个 artifact_ids）。

## 规则

- 必须写清 surface_similarity vs structural_similarity
- 必须有可证伪假设
- 禁止只有词汇相似而无可迁移机制
