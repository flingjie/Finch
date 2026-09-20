---
name: connection-opportunity
description: >
  判断连接机会：对方最近在解决什么、用户有哪些真实经验可贡献、为什么现在值得互动、
  不互动的理由、最小连接动作。无贡献点时输出 SKIP。
---

# connection-opportunity

输入：PeerProfile + 对方工件摘要 + 用户可追溯证据 refs。
输出：ConnectionOpportunity（decision=connect|SKIP）。

## 规则

- 用户经验必须能追溯到 user_evidence_refs
- 无贡献点 → SKIP，不强行生成空泛夸赞
- 最小动作默认：公开回复并补充自己的真实经验
- 禁止调用 OpenCLI reply/post

## CLI

- `finch connections today` 展示需回应/兑现的真实承诺（关系域投影，非发现排序）
- 深度准备仍可走 `finch connect prepare`（并存）
