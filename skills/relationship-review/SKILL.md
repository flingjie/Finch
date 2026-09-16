---
name: relationship-review
description: >
  根据互动记录判断是否存在自然的下一次交流理由、是否进入 recurring/practicing、
  是否暂时转为 dormant、哪些信息不应被当作关系进展。
---

# relationship-review

输入：PeerProfile + 双向交流次数 + 是否启动实验/共同产出 + 自然后续理由。
输出：RelationshipReview（suggested_stage / should_contact）。

## 升级规则

- engaged：用户确认已进行一次公开互动
- recurring：至少两次有内容的双向交流
- practicing：围绕共同问题启动了实验或验证
- collaborating：共同产生了可见成果
- 时间陈旧 alone 不触发对外联系；可建议 dormant

## 边界

- topic-dialogue 模拟讨论不是关系进展
- 不自动发消息
