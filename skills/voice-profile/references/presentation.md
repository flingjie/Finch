# voice-profile 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
画像里目前有 12 条偏好、4 条避免表达、8 个已批准样例。

最值得先确认的更新候选：

1. 避免：「本质上」— 你在采用稿里删过
2. 偏好：「先讲场景再下判断」— 你改向过多次
3. 避免：「赋能」— 单次删除，证据偏弱

这些不会自动写入。完整 YAML 默认不贴。

回复「采纳 1 和 2」「全拒」或「看完整画像」。
```

素材来自 `finch voice show` 摘要与 `voice propose` 更新候选（每类最多 3 条）。

## 用户下一轮 → CLI

- `看完整画像` → `uv run finch voice show --json`
- `提出更新` → `uv run finch voice propose`
- `记入采用样例` → `uv run finch voice approve-example {draft_id}`
- `记入拒绝样例` → `uv run finch voice reject-example {draft_id} --reason "..."`
- `采纳 N` → 用户确认后人工改 `voice-profile.yaml`（propose 只读，不自动写库）
