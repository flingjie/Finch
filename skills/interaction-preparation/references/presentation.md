# interaction-preparation 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
为你准备了 5 条互动建议，最值得先发的是：

**回复 @alice：关系记忆不只是存聊天记录** 〔最推荐〕

为什么是这个人：对方正在问「连接之后系统该记住什么」，和你的实践直接对得上。
为什么现在：帖子还在讨论中，晚一天就变成旁观者评论。
预期开口：先对齐问题，再给一个你验证过的小结论，并留一个可反驳点。

草稿预览：
> 你们说的「记住」如果只是日志，关系还是冷的。我们试过把每次互动落成「聊过什么 / 新认识 / 下次为何值得继续」三条事实，后面跟进才有抓手——你们这边有没有卡在「存了但用不起来」？

事实风险：草稿里的「我们试过」需对应用户真实证据；没有就改成提问。

另外两条可选：

2. 引用 @bob 的失败复盘 — 贡献类型是 counterexample，语气更冲
3. 观察 @cara 的长帖 — 先 bookmark，暂不公开回复

其余偏弱信号：4…

回复「批准 1」「改草稿 1」「跳过 2」或「换一批」。
```

素材来自 `finch connect prepare` / `create` 决策卡（动作 / 为什么是这个人 / 为什么现在 / 草稿预览；卡末批准命令带真实 id）。主文不贴 `proposal_*` id。

## 用户下一轮 → CLI

- `批准 N` → `uv run finch connect approve {proposal_id}`（批准只记发布意图，不等于已发出）
- `改草稿 N` → 把修订写入文件后 `uv run finch connect edit {proposal_id} --file <path>`
- `跳过 N` → `uv run finch connect reject {proposal_id} --reason "..."`
- `换一批` → 再跑 `uv run finch connect prepare`，或从本次未展示提案再挑最多 3 条
