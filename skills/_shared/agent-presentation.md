# 向用户呈现（共享原则）

Skill 调用 Finch CLI 之后，目标是帮用户做选择，不是汇报系统过程。

用户回复的结构固定为：

1. **结论**：范围与数量一句话 + 点名最值得继续的一条及理由。
2. **决策卡**：
   - **浏览列表**（peer-discovery / connect today）：可展示 8–12 张轻量卡；不要为整表生成完整回复。
   - **深度准备**（interaction-preparation / connect prepare）：主视觉最多展开 **3** 条；其余折叠为一行列表。
3. **操作**：自然语言下一步（各 skill 自己的动词，见各 skill 的 `references/presentation.md`）。

## 约束

- 开场只报范围与数量；不写 CLI / 翻库 /「挑末尾」等检索过程。
- 必须点名一条首选，理由落到关系价值、可贡献空间或产品方向。
- 技术 id（`peer_*` / `opp_*` / `proposal_*` / `idea_*` / `thread_*` / `snapshot_*`）不写在标题里；agent 内部保留「序号 → id」映射，换一批后不能选错人。
- 失败只说改变了结果的失败与重试；工具读写成功不必报。
- 本文件只定原则；动作词与命令映射写在各 skill 的 `references/presentation.md`。
- 「主视觉最多 3 条」约束的是**深度准备**，不是每日浏览名额。
