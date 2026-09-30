# Finch 交流机会设计落地对照（v1.1）

日期：2026-09-30  
对照：`Downloads/Finch-Conversation-Opportunity-Detailed-Design.md` + 本仓库实现。

## 已交付

| 规范 | 实现 |
|---|---|
| §10.1 / §11 机会聚合 | `src/finch/opportunities/` 状态机 + events + 写锁 + request_id 幂等 |
| §5.5 首选 0–1 + 顺序回退 | `daily.py` 评估 top-N；`opportunity_assess_limit`；skip/eval_failed 区分 |
| §5.5 为何优先 | 快照持久化 `opportunity_assessments`；文本渲染跳过原因 |
| §11.3 skip 幂等 | `SkipAssessmentRepository`（仅缓存 skipped，不缓存 eval_failed） |
| §6 / §10.3 按需贡献 | `connect prepare` → Artifact + ready；`material_origin` / `execution_status` |
| §6.4 演示事实回填 | `connect artifact-status`（唯一合法 ran_* 写入） |
| §4.2 入口 2 | `connect assess --url` |
| §4.2 入口 3 | `drafts create --allow-unconfirmed`（既有） |
| §7.1 用户材料 | prepare 注入 voice + 已确认 AuthorPosition |
| §6.1 成本与未知 | `Proposal.cost_note` |
| §10.4 / §14.3 接续 | `connections follow-up` + `previous_opportunity_id` |
| §14.2 漏斗 | `finch weekly` 机会漏斗；呈现首选优先用 PresentationRecord |
| §13 发现时限 | `discovery.discovery_deadline_seconds=180`，评估循环 soft-stop |
| §5.4 暂缓配套 | prompt 要求未核对线程回复写入 `open_questions` |

## 明确暂缓（及重新考虑条件）

| 项 | 条件 |
|---|---|
| §5.4 / §9.2 证据扩展 agent loop | 首选选中率低且 skip 集中于「无法判断重复/已解决」 |
| 发现总时限的检查点恢复 | 真实运行频繁超限且需要中断后续跑 |
| 一次 prepare 组合多成果 | 单一成果不足以支撑审阅 |
| 周期自动读回应 | 用户明确授权后台刷新 |

## 验收清单速览（§16）

- [x] 多种入口（含 URL）；不强行套痛点
- [x] 首选含双方理由 + 最小贡献 + 成本与未知
- [x] 证据分层 + open_questions；无首选可为空
- [x] 选定后 prepare；直接写作短路径保留
- [x] ready ≠ 发布；artifact-status 回填演示
- [x] 模型不改状态；幂等 / 冲突显式
- [x] 同一回应不重复计数；接续可追溯
- [ ] 线程回复预算内扩展（暂缓）
