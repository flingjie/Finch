# 交流信号 → Idea 的判据（conversation 来源）

输入是已验证的 ConversationEvidence（verified=True）。提炼时：

- 外部作者的经历不写成用户经历；进入 core_point/reader_problem/boundaries 的表述中性化。
- 原文只保留在 source_refs.summary。
- 未经验证的证据拒绝提炼（`finch ideas create --conversation` 会拒绝 verified=False）。
