# problem-discovery Skill-first 影子实验结果

日期：2026-10-30  
Harness：`tests/unit/test_problem_discovery_shadow_experiment.py`  
Fixtures：`tests/fixtures/problem_discovery_shadow/manifest.json`（10 条）  
设计：`docs/superpowers/specs/2026-10-30-skill-first-thin-runtime-design.md`

## 实验设置

| 路径 | 实现 |
|---|---|
| Control | `discover_problems` + 确定性 FakeRunner（模拟 `assess_problems` 输出形状） |
| Shadow | 宿主风格草稿 JSON → `validate-upsert --shadow`（`build_problem` 门禁） |
| 对照 | 同一 `_collect_sources` 池 / fingerprint |

说明：本轮 harness **未**嵌入真实宿主 Cursor/Codex 会话；用可编程草稿验证 thin tools、门禁与 compare。真实宿主 Skill 跑分仍需人工按 `skills/problem-discovery-shadow/SKILL.md` 执行。

## 指标（harness）

| 指标 | 结果 | 相对目标 |
|---|---|---|
| 样本数 | 10 | 8–12 ✓ |
| fingerprint 一致 | true | 必须 ✓ |
| Control 卡片数 | 10 | — |
| Shadow 卡片数 | 11（含 1 张故意坏摘录卡） | — |
| 坏摘录证据 | 强制清空 + 「未验证」 | ✓ |
| 发明 URL 存活率 | 0（evil.example 被来源 URL 覆盖/丢弃） | ≤ control / 目标 0 ✓ |
| Control 证据条数 | ≥10 | ✓ |
| Shadow 有效证据条数 | ≥10 | ✓ |
| Gate drop 可观测 | `ValidateUpsertResult.dropped` 记录 | ✓ |
| 人工救火（本 harness） | 0 | n/a for scripted |

## Go / No-go（Phase 2）

**判定：Conditional Go（有条件进入下一阶段准备，不删除 control LLM 抽取）**

理由：

1. Thin tools（candidates / validate-upsert / sync / journal / compare）可用，影子命名空间不污染 control。
2. 证据门禁在影子路径上行为正确（坏 quote / 假 URL / 假 ref）。
3. **尚未**用真实宿主 Agent + 真实讨论完成 8–12 条人工质量对照（问题质量、Token、救火次数）。

**在删除 `assess_problems` / `discover_problems` 之前必须：**

1. 用 `problem-discovery-shadow` Skill 对同一 fixture（或线上样本）跑完整宿主会话。
2. 人工 spot-check ≤3 重点问题质量 ≥ control。
3. 记录 Token / 墙钟 / 救火次数；救火频繁则 No-go，迭代 Skill。

## 已知限制

- Control harness 用 FakeRunner，不代表生产 Codex 抽取质量。
- `list`/`summary` CLI 默认仍读 control inbox；影子查看依赖 `compare` 或直接读 shadow YAML。
- PD `sync` 依赖 opencli/网络；fixture 实验只用 `add --text`。

## 下一轮建议

1. 一次真实宿主影子会话（Browser-use / agent-stuck 主题）并填质量表。
2. 若质量达标：再拆 `products/feedback` 分析层为 Skill + thin validate。
3. 暂缓 Product↔Problem 关联大功能，直到 Skill-first 研究路径稳定。
