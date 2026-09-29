# idea-discovery 呈现配方

见 `_shared/agent-presentation.md` 的共享原则。本文件是形状真源与命令映射。

## 形状

```text
这段材料我看出 3 个可能观点；我推荐第 2 个，因为它能给同样处理批量任务的人一个
可验证的改法。不过，性能收益目前只由这次运行支持。

〔推荐〕2. 小型、重复的结构化提取可以考虑批处理
   情境 / 所得 / 证据 / 边界 …
  1. 并发任务慢，先拆分推理时间与进程启动时间
  3. 批处理会放大单次失败的影响，需要限定批大小
（淘汰：…）

回复「写 2」「改选 1」「展开 2」或「换一批」。
```

素材来自 `finch ideas commit` 的发散结果（我看出 N 个可能观点，推荐第 X…）；用自然中文改写，代码标识可保留。

## 用户下一轮 → CLI

- `写 N` → `uv run finch ideas confirm {id}`
- `改选 N` → `uv run finch ideas choose {exploration_id} N`
- `展开 N` → `uv run finch ideas show {id}`
- `比较 A 和 B` → 各一句差异 + 哪条更贴当前定位
- `换一批` → 从本次未展示候选再挑最多 3 条；没有则说明并给 `uv run finch ideas list`
