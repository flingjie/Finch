# 问题观察日志 + 投影（阶段 B）设计

日期：2026-11-02
状态：设计已评审（brainstorming）
执行顺序：三项优化中第 3 个（cli 拆分 → 工件索引 → 问题事件流）
前置：阶段 A（source_checked / 作者绑定）已合入 main。

## 1. 问题与第一性结论

问题卡片目前是「可变的最新快照」：`problem_id = hash(标题)`，upsert 整卡覆盖。
人工进展保护、冲突保留、幂等去重、崩溃恢复全是对这个根因的补丁。
本轮把存储改为**不可变观察历史（事件流）+ 派生投影（卡片）**：
刷新 = 追加观察 + 重建投影，不再改写历史。

## 2. 存储布局

```
var/problem_discovery/problems/<id>.yaml   # 投影（物化视图；所有现有读方不变）
var/problem_discovery/events/<id>.jsonl    # 每问题一个追加式事件流（唯一真源）
```

写路径：追加事件（`Workspace.append_jsonl`）→ 从全事件流确定性重建投影 →
`write_yaml` 覆盖投影。读路径（list/show/summary/present/Skill）完全不变。
shadow 命名空间同构（`problem_discovery/shadow/events/`）。

## 3. 事件模型（本轮只五种）

| 事件 | 写入者类别 | 载荷 |
|---|---|---|
| `observation` | discover / validate-upsert | 过门禁后的整张草稿字段 + 证据、来源指纹、run_id、host_authored |
| `human_decision` | 用户（admit CLI） | accepted、理由 |
| `title_edit` | 用户 | 新标题（ID 不变） |
| `merge_redirect` | 用户确认的合并 | 目标 problem_id（本卡转重定向） |
| `conflict_flag` | 宿主 | 证据去重键、claim_support 变化、理由 |

每条事件：`event_id`（载荷内容哈希 → 同一事件重复追加被拒，天然幂等）、
`at` 时间、`writer` 类别（采集边界 / 确定性代码 / 宿主判断 / 用户——字段写入者
四分法落地）。阶段 C 的判断 / 实验 / 决策与长期研究计划的授权 / 决定卡片，
都是未来**新增事件类型**，不改本结构。

## 4. 身份与匹配

- 既有 `problem_id` 全部保留，不迁移改名。
- 新问题 ID = 创建时规范化标题哈希，**创建后冻结**；改标题（`title_edit`）不换 ID。
- 「来源位置 → problem_id」映射由事件流派生（投影侧维护）；一个来源可对应多个问题。
- 重抽匹配规则（确定性，按序）：
  1. 规范化标题与既有问题相同 → 同一问题（追加 observation）；
  2. 否则若与某既有问题 ≥1 个共享来源位置 **且** ≥1 条去重键相同的证据 → 同一问题；
  3. 其余创建新卡；若与既有卡有来源重叠，在投影标「可能重复」（仅建议，不自动合并）。
- 跨来源 / 相似标题合并只经人工确认，写 `merge_redirect`；旧 ID 永久可解析为重定向，
  已有展示顺序与引用不失效。

## 5. 投影重建规则

- 描述字段（user_type / scenario / hypotheses / unknowns / next_action）：
  **最新 observation 胜出**；旧值保留在事件历史，可查可回滚。
- 证据：按 `(来源位置, 规范化摘录)` 去重键跨观察累积；内容指纹未变不重复追加。
  已失效证据不删除，标记退出当前支持集合。
- `accepted` 等人工状态只能来自 `human_decision` 事件，任何 observation 不能覆盖——
  保护由结构给出；现有「ACCEPTED 不被降级」的特判代码删除。
- 新观察与旧结论冲突：双方事件并存，投影标「需复查」（结合 `conflict_flag` /
  claim_support），不静默覆盖。
- `created_at` = 首条事件时间，永不漂移。admission / claim_support / E2 门禁
  沿用阶段 A，不变。

## 6. 一次性迁移

`finch problem-discovery migrate-events`（一次性命令）：

1. 备份整个 `problems/`（含 shadow）目录；
2. 每张现有卡生成 genesis `observation` 事件（accepted 卡补一条 `human_decision`）；
3. 从事件流重建投影，与原卡逐字段比对（忽略字段顺序），全部一致才落段；
4. 任何不一致 → 中止并回滚，不留半迁移状态；
5. 迁移完成后，`discover` / `validate-upsert` / `admit` 只走事件路径。

## 7. 验收（对应 2026-11-01 计划阶段 B 清单）

- 改标题不丢进展（证据、accepted、展示引用均在）。
- 相同输入重跑不增加重复证据或重复事件（event_id 幂等）。
- 同标题不同场景不被误合并；同来源的不同问题可分别存在。
- 人工状态与（未来的）实验结果不因刷新清空。
- 并发旧写入不能覆盖新决定（追加式 + 重建天然满足；同问题写入串行化）。
- 人工合并后旧 ID 可解析重定向。
- 迁移：备份存在、比对通过、失败回滚可验证。

## 8. 文档

- README / docs 增加一小节 `var/` 布局说明：活跃域 vs 冻结遗留；
  `events/` 是真源、投影与 `index.jsonl` 是派生物。

## 9. 不做

- 不建全量 embedding / 语义聚类；相似标题只给「可能重复」建议。
- 不加实验 / 决策 / 授权事件（阶段 C 与长期研究计划）。
- 不迁移冻结域数据，不重排 `var/` 顶层目录。
- 不引数据库；仍是文件工作区 + 原子写。
