---
name: creator-evidence
description: >
  从有限的跨平台工件集合中提炼创作者证据卡（CreatorEvidence）。每项判断必须引用
  artifact_id。输出一手程度、可信度与反证。禁止自行调用写操作或修改关系状态。
---

# creator-evidence

输入：一个人的有限证据集合（RawArtifact / artifact_id 列表）。
输出：`CreatorEvidence`（kind / claim / support / first_hand / confidence / counter_evidence）。

## 规则

- 每项判断必须引用 `artifact_id`
- kind ∈ creation | first_hand_experience | knowledge_sharing | cross_domain_bridge | conversation_behavior | marketing_or_repost
- 营销/搬运标为 marketing_or_repost（反证）
- 不调用 OpenCLI 写命令；不修改 PeerProfile.relationship_stage

## CLI

由领域服务在 shortlist / connections 路径调用；本 Skill 只做认知判断。
