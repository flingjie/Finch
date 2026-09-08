# 去除数据库、改文件工作区 设计

日期：2026-09-08
状态：待用户评审

## 1. 目标（一句话）

> Finch 可以没有数据库，但不能没有记忆。真正要删的是数据库基础设施（SQLite / SQLModel / Alembic），不是领域状态。

把持久化从「套着 SQLModel 的 JSON 文件仓库」迁移为 **Agent Workspace**：文件成为 Agent 可直接理解的长期记忆，确定性 Python 继续守住状态机、幂等、门禁和安全边界。产品契约（`docs/product-contract.md`）的「LLM 负责候选与语义判断，Python 负责状态、幂等、去重与门禁」不变。

## 2. 决策记录

| # | 决策点 | 结论 |
|---|--------|------|
| D1 | 范围 | **完整打包**：存储层替换 + CLI Agent 化 + projections，一份 spec 分 6 个检查点落地。 |
| D2 | 现有数据 | **全新开始**：不写迁移、不归档 `var/finch.db`，直接删除。关系域（peer/thread/interaction）当前 0 行，其余（proposal 12、draft 2、critic 7、evidence 102）可重建或已过时（`interactioncandidaterecord`/`opportunityrecord` 已是孤儿表）。 |
| D3 | 并发 | **仅原子写**（临时文件 + `os.replace`）。不做文件锁；单用户本地 CLI、单进程。若未来多 Agent 并发写才加锁。 |
| D4 | 序列化 | **YAML/mixed**：状态对象 YAML，草稿正文 Markdown，追加历史 JSONL，投影/缓存 JSON。 |
| D5 | 工作区根目录 | **沿用 `var/`**（已是 `Paths.var_dir`、已被 `.gitignore` 忽略、零改动）。`.finch/` 只是命名偏好，如需可一行改名。 |
| D6 | 草稿文件形态 | **单文件 frontmatter `.md`**（`---` YAML 元数据 `---` + Markdown 正文），不用 `draft.md` + `metadata.yaml` 双文件。原因：双文件无法单次原子写，会破坏「原子写」这一唯一一致性机制。 |
| D7 | 仓储接口 | **保留全部 Repository 类名与公有方法签名**，只改构造入参（`Store`→`Workspace`）与内部实现（`Session.merge/commit`→文件读写）。 |
| D8 | ID 与文件名 | **文件名 = 既有领域 ID**（`peer_id_for`/`thread_id_for`/`generation_key`/`ContentJob.id` 等已确定），不新增哈希层；仅加 `_safe_filename` 做 `..`/`/`/NUL/`:` 消毒。 |

## 3. 为什么现在适合去数据库

当前 15 类 Record 几乎都是 `id + 少量索引字段 + payload_json + updated_at`；`DraftRepository.list_by_job()` 已经「全表读 + `payload_json` 过滤」，说明是在用 SQLite 模拟文档存储，而非真正用关系关联。附带成本已开始反噬产品速度：

- SQLModel / SQLAlchemy / Alembic 三件套 + 9 个迁移文件 + `alembic.ini`/`env.py`/`script.py.mako`。
- `database.py` 里已有 `prune_orphan_tables` / `prune_legacy_content_jobs`（schema 漂移 / legacy 行清理）。
- Record 模型与领域模型双重维护、数据结构变化的迁移兼容。

Finch 是单用户、本地 CLI、小数据、低并发、Skill + 领域服务架构，主要由 Codex/Agent 使用 —— 更适合文件工作区。

## 4. 架构：`Workspace`

用 `Workspace`（文件系统）替换 `Store`（SQLite）。`Workspace` 是薄的确定性对象，持有根目录并提供三个原语：

- `ensure()`：幂等创建目录树。
- `atomic_write(path, text)`：写 `path.tmp`（同目录）→ `os.replace`。这是**唯一**一致性机制。
- `read_yaml(path, Model)` / `write_yaml(path, model)`：`model_dump(mode="json") → yaml.safe_dump` / `yaml.safe_load → Model.model_validate`，含钉死的往返保证（§6）。

新建 `src/finch/storage/workspace.py`；删除 `src/finch/storage/database.py`。

## 5. 文件布局

```
var/                                  # 工作区根（gitignored）
  peers/<peer_id>.yaml                # PeerProfile
  conversations/<thread_id>.yaml      # ConversationThread（当前状态）
  conversations/<thread_id>.events.jsonl   # 追加式互动事件
  interactions/
    proposals/<id>.yaml               # InteractionProposal
    records/<id>.yaml                 # InteractionRecord
    evidence/<id>.yaml                # ConversationEvidence
    snapshots/<id>.yaml               # FeedbackSnapshot
    run-stats/<id>.yaml               # EngagementRunStats
  ideas/<job_id>.yaml                 # ContentJob（idea 状态机）
  drafts/<draft_id>/
    draft.md                          # YAML frontmatter + Markdown 正文
    critic.jsonl                      # 每轮一行
    versions/<round>.md               # frontmatter + 正文快照
  evidence/<card_id>.yaml             # EvidenceCard
  feedback/<draft_id>.yaml            # Feedback
  publication-intents/intent_<source_id>.yaml
  decisions/dec_<job_id>.yaml         # DecisionRecord（id 已含 dec_ 前缀）
  practice/<session_id>.yaml          # PracticeSession
  projections/*.json                  # 可重建派生上下文（§8）
  cache/github/  cache/twitter/       # 已有，不变
  inbox/  outputs/                    # 已有，不变
```

**文件名 = 既有领域 ID。** 复合 ID 自然拆解：`draft_id:round` → `versions/<round>.md`（round 已是字段）；`intent:<source_id>` → `intent_<source_id>.yaml`。`DecisionRecord.id` 已含 `dec_` 前缀、`InteractionRecord.id` 已含 `rec_` 前缀，无需额外处理。`_safe_filename()` 拒绝 `..`/`/`/NUL，把 `:` 映射为 `_` 作为兜底。

## 6. 序列化与往返保证

| 数据 | 格式 | 原因 |
|------|------|------|
| Peer / Idea / Proposal / 状态对象 | YAML | 结构明确，Agent 与人可读 |
| 草稿正文（Draft） | frontmatter `.md` | 正文直接编辑 / diff / 复用；单文件单真相单原子写 |
| 对话事件 / Critic 轮次 / 反馈历史 | JSONL | 追加、保序；每行带显式 `ts` 支撑时间窗过滤 |
| projections / 缓存 | JSON | 可重建，非事实源 |

**往返保证（测试钉死）：** `write_yaml(m)` 再 `read_yaml` 返回相等模型；同一模型写两次字节一致。datetime 序列化为 UTC ISO-8601，`StrEnum` 存 `.value`，`None` 存 `null`，`Literal`/嵌套模型走 `mode="json"`。读取用严格 `yaml.safe_load` + `model_validate`（不用 `model_construct`），杜绝部分解析。

## 7. 仓储层重写

**保留全部 ~20 个 Repository 类名与公有方法签名**，只改构造入参（`Store`→`Workspace`）与内部实现。服务层 `IdeaService` / `DraftService` / `PracticeService` / `InboxService` / `WeeklyReflectionService` 及其测试**几乎不动**；只有 `cli.py` 的 ~40 处 `Store(settings.paths.db_path)` 换成 `Workspace(settings.paths.var_dir)`。

查找映射到文件扫描：

- 主键 `get(id)` → 读 `<dir>/<id>.yaml`。
- 二级查找（`find_by_generation_key`、`list_by_peer`、`list_by_proposal`、`list_pending`/`list_executed`、`list_unverified`、`list_by_interaction`、`list_by_job`、`list_versions`、`list_all_reports(since)`）→ `sorted(glob(dir/*.yaml))` + Python 过滤。每类对象独立目录，扫描有界。幂等 upsert 免费获得：确定性文件名 = 确定性 id → 写覆盖。

错误处理：缺文件 → `get` 返回 `None`；损坏/不可解析文件 → `list` 跳过（沿用现有 `_list_jobs_and_failures` 行为），经 `parse_failures` 钩子暴露；写入异常传播 `OSError` 并清理临时文件。

## 8. CLI Agent 化 + projections

两部分，均为**加法**，不破坏现有命令：

1. **`finch context`**（确定性 Python 生成，原子写，可删）：
   - `daily-context.json`：今日相关 peers + 待继续 threads + 待确认 ideas + 待处理 proposals —— Agent 的窄入口，避免扫全历史。
   - `pending-actions.json`：已批准未执行 proposals、未确认 ideas、待审 drafts。
   - `weekly-summary.json`：周复盘输入（复用现有指标聚合）。
2. **加性 Agent 命令**，每个都是对既有领域服务的薄包装（所有写入经 CLI/服务，LLM 不直接改文件）：
   - `finch context daily`
   - `finch peers get <id> --json`、`finch conversations get <id> --json`
   - `finch proposals create/approve/reject/edit <id>`
   - `finch interactions record <id> --url …`（单命令聚合：校验已批准 → 建 InteractionRecord → 更新 ConversationThread → 追加事件）
   - `finch ideas confirm <id>`、`finch drafts create <idea_id>`

命令名映射到**既有** `finch connect / ideas / review / drafts`，不另造平行面；原则是「加性 + `--json`」。精确别名表在实施计划里定。

## 9. 删除清单

- `storage/database.py`（Store、`prune_orphan_tables`、`prune_legacy_content_jobs`、`init`）。
- `repositories.py` 中全部 `*Record` SQLModel 类（`*Repository` 类保留，改为文件实现）。
- `alembic.ini`、`alembic/`（9 个版本 + `env.py` + `script.py.mako`）。
- 依赖 `sqlmodel`、`sqlalchemy`、`alembic`（保留 `pyyaml`）；`pyproject.toml` 里 `alembic/versions` 的 lint 排除。
- `settings.py` 的 `db_path` 字段。
- 测试 `test_alembic.py`、`test_database.py`、`test_storage.py`。
- `finch init --prune` 的 prune 行为（无 schema 可清理）；`init` 退化为 `workspace.ensure()`。

## 10. 不变量（保持不变）

证据优先、不自动发布、外部≠证据、确定性总分、子进程纪律全部不变。文件层只改变**状态存在哪里**，不改变**谁判定状态合法**。

## 11. 测试

- 仓储测试把 `Store(tmp_path/"db.sqlite")` → `Workspace(tmp_path)`，**断言不变**（断言即行为契约）。
- 新增：每模型往返测试、原子写崩溃测试（临时残留 + 原文件完好）、投影生成测试、`finch context` CLI 测试。
- CLI 测试把 `Store(...).init()` → `Workspace(...).ensure()`。

## 12. 实施检查点（单 spec，逐点可绿）

1. `Workspace` + 原子写 + YAML/JSONL 助手（无行为变化）。
2. 逐个仓储改写为文件实现，测试绿。
3. 重接 `cli.py` `Store`→`Workspace`，所有既有命令通过。
4. 删数据库基础设施、依赖、死代码；`ruff` + `mypy` 绿。
5. 加 projections + `finch context` + 加性 Agent 命令。
6. 全量 `uv run pytest && uv run ruff check . && uv run mypy src` 绿。

## 13. 范围外 / 恢复数据库的触发条件

不恢复 Graph Runtime，不引入新 Agent 框架，不把状态判定与发布门禁交给 LLM。仅当 Finch 演变为 Web 服务、多用户、多 Agent 并发写、数万级 peer/互动、复杂筛选统计全文搜索、云端同步与权限控制时，才值得恢复数据库——这些都不是当前阶段需求。
