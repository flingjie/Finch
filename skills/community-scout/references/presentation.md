# 社区行动卡呈现配方

形状真源与命令映射。共享呈现原则见 `_shared/agent-presentation.md`（结论 → 决策卡 → 操作）。

默认先给：一句明确建议 + 必要公开来源 + 一个下一步；详情按需展开，不固定七字段。

## 四种呈现

### 1. 周报（本周探索）
- 最多 3 个不同参与价值的社区，点名首选及原因；合格不足就如实给实际数量，不凑数。
- 每个给：这是什么社区、为什么现在适合、近期公开证据（带 url）、一个下一步。
- 有 `actionable` 社区给确切入口；只有 `observe` 就说"可观察，暂无切入点"。

### 2. 按问题探索
- 优先 1 个能回应此问题的当前讨论，再给 1 个不同路径的备选。
- 没有现成讨论 → 提出观察/贡献路径，不编造切入点。

### 3. 选中深读
- 读若干相关公开讨论与关键 Builder，说明已有方案、用户可能补的缺口、无法确认之处。
- 没有实质缺口 → 建议观察或转向备选。

### 4. 回访
- 读历史选择、实际反馈与新的公开证据，给继续/观察/暂缓建议。
- 不把旧加入状态等同于活跃交流；已互动优先读未完成讨论与承诺，不重推"首次加入"。

## 用户回复 → CLI 映射

| 用户说 | 记录命令 |
|---|---|
| 不感兴趣 | `finch community feedback <id> --result ignored` |
| 以后可能参与 | `finch community feedback <id> --result saved` |
| 先观察，暂时不发言 | `finch community feedback <id> --result saved --reason-kind deep_but_later --note "先观察"` |
| 已加入 / 开始关注 | `finch community feedback <id> --result joined` |
| 完成第一次公开互动（有链接） | `finch community feedback <id> --result interacted --ref <公开链接>` |
| 和成员第二次交流 | `finch community feedback <id> --result repeated --ref <链接>` |
| 提交了代码/案例/工具 | `finch community feedback <id> --result contributed --ref <链接>` |
| 没时间 | `finch community feedback <id> --result saved --reason-kind no_time` |

## run 产物

`finch community run` 落地决策记录（`runs.jsonl` + `steps.jsonl`）。复盘用 `finch community run-trace
<run_id>` 看每个动作（search/inspect/propose/finish）的 decision 与 outcome，区分「抓取范围太窄」
「判断标准有偏」「切入话题差」。