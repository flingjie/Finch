# conversation-follow-up 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
有 4 条对话需要跟进，最该先回的是：

**与 @alice：关系记忆要落成哪些事实？** 〔最推荐〕

未解问题还在：互动之后系统该记住什么、什么不该记。你们已有一点共识（要可继续，不要纯日志），分歧是「事实条目」够不够。

建议下一步：直接回答未解问题，并提议一个小实验——下次互动后只记三条：聊过什么 / 新认识 / 下次为何继续；问对方是否愿意试一轮。

另外两条可选：

2. 与 @bob：失败复盘是否该写成公开帖 — 超期未动，先确认还开不开
3. 与 @cara：评测指标争论 — 可跟进，但没有未解问题那么急

其余可先放着：4…

回复「跟进 1」「展开 2」「先放着」或「换一批」。
```

素材来自 `finch conversations list --needs-follow-up` / `show` / `follow-up` 决策卡（话题 / 未解问题 / 建议下一步）。主文不贴 `thread_*` id。

## 用户下一轮 → CLI

- `跟进 N` → `uv run finch conversations follow-up {thread_id}`，把 `next_step` 用人话写成可发送的下一句（不自动发出）
- `展开 N` → `uv run finch conversations show {thread_id}`，补未解问题 / 共识 / 分歧 / 可实验
- `先放着` → 不改库；若用户明确关闭意图，说明需人工更新线索状态（本 skill 不写库）
- `换一批` → 从本次未展示的待跟进线索再挑最多 3 条；没有则给 `uv run finch conversations list --needs-follow-up`
