# Practice Profile：让 Finch 认识用户的真实实践

日期：2026-10-01
状态：已确认（待写实现计划）

## 1. 背景与问题

`var/` 中的真实数据（2026-09-30）显示发现漏斗上宽下空：

- 2573 个 `PeerProfile`、4006 个 `RawArtifact`、62 张 `CreatorEvidence`
- 2 个 `Opportunity`（`decision` 均为 null）
- 0 个 `ConversationThread`；`voice-profile.yaml` 全空；154 个 idea 只确认 2 个
- 旧管线留下的 5 条 connection 记录全部 `SKIP`，理由完全一致：
  「用户证据为空，无法基于真实实践提供贡献点」

根因不在发现层，而在 **Finch 不知道用户是谁、做过什么**。当前机会评估与贡献制作关于用户的输入只有三个口，且几乎为空：

| 输入口 | 来源 | 现状 |
|---|---|---|
| `user_context` | `--question` 或 `interests.current_questions[0]` | 只是一句问题，无经历 |
| `user_positions` | 已确认 `ContentJob.author_position` | 154 个 idea 仅 2 个确认 |
| `voice_summary` | `voice-profile.yaml` | 空 |

`interests.practice_refs` 只被 community-scout 读取；机会评估和贡献制作不看它。两个 prompt
（`opportunity.md` / `prepare-contribution.md`）都明令「不得编造用户经历」，模型在没有材料时唯一诚实的选择就是 SKIP。

用户画像：药学本科，爱好阅读、健身、编程；擅长学习与实践，不擅长输出与分享；出版《自学区块链》；
`flingjie/Agent-100-Days`（753 star）；`flingjie/InvestAI`（161 star）。而 `finch.yaml` 的
`repositories` / `practice_refs` 只指向 0-star 私人实验仓库，这些公开资产一个都没列。

## 2. 目标与非目标

**目标**

- 建立用户真实实践的唯一真相源 `practice-profile.yaml`，由用户确认后才生效。
- 把已确认条目注入机会评估（`why_me` 挂钩）与贡献制作（允许有边界的第一人称），终结「全部 SKIP」。
- 提供 `finch profile init` 从公开仓库 README 起草初稿，降低「不擅长输出」用户的首次填写成本。
- 用户经历范围：跨领域全量（Agent 工程 / 投资纪律 / 区块链自学与出版 / 药学背景 / 健身与阅读的长期实践）。

**非目标（YAGNI）**

- 不自动 confirm 任何条目；不从对话、反馈、发现结果自动更新 practice profile（与 `VoiceProfile` 同规则）。
- 不改 `interests` / `sources` 查询词、不动 50 人分层、不清理 `finch.yaml` 配置债 —— 跨领域重定向另开 spec。
- 不新增外发能力；`gh api .../readme` 只读。
- 不走 commit → EvidenceCard 管线提取用户经历（该路径只产技术证据，且 confirm 环节正是用户卡住的地方）。

## 3. 数据模型

新增 `src/finch/profile/`（与 `content/voice.py` 同范式：Pydantic 模型 + YAML 加载器）。
注：`src/finch/practice/` 已被 expression-practice 的 `PracticeService` 占用，故模块名用 `profile`，与 CLI `finch profile` 对齐。

```python
class PracticeEvidenceStatus(StrEnum):
    SOURCED = "sourced"              # 有公开 URL 可引用
    AUTHOR_STATED = "author_stated"  # 用户亲口陈述，无公开证据（如药学背景）

class PracticeItem(BaseModel):
    id: str                          # slug，如 agent-100-days
    domain: str                      # 领域标签：agent engineering / pharmacy / self-learning / fitness
    claim: str                       # 一句话：我真的做过什么、学到什么
    evidence_refs: list[str] = []    # 公开 URL；为空则 status 必为 author_stated
    status: PracticeEvidenceStatus
    can_offer: list[str] = []        # 可贡献形式：方法卡 / 案例 / 对比 / 反例 / 澄清问题 / 跨领域类比
    boundaries: str = ""             # 明确不能替用户说的话，如「没管过生产 Agent 的 SLA」
    confirmed: bool = False          # 只有 True 的条目进入任何 prompt

class PracticeProfile(BaseModel):
    items: list[PracticeItem] = []
    def confirmed_items(self) -> list[PracticeItem]: ...
    def is_empty(self) -> bool: ...  # 无 confirmed 条目即为空
```

**校验规则**

- `evidence_refs` 为空 ⇒ `status` 必须为 `author_stated`；`status: sourced` 而 refs 为空 → 该条目校验失败。
- `id` 在文件内唯一。

**文件位置**：仓库根 `practice-profile.yaml`，路径由 `settings.paths.practice_profile_path` 给出（默认即此）。

**加载**：`load_practice_profile(path) -> PracticeProfile`。文件缺失 / 空 → 空画像；下游行为与今天完全一致。

示例：

```yaml
items:
  - id: agent-100-days
    domain: agent engineering / teaching
    claim: 把 2024 年至今 Agent 落地的失败（Prompt 当逻辑层、工具列表失控、能力上限不清）整理成 100 天学习路径
    evidence_refs: [https://github.com/flingjie/Agent-100-Days]
    status: sourced
    can_offer: [方法卡, 案例, 对比]
    boundaries: 没有管过生产 Agent 的 SLA；不替企业级多租户场景发言
    confirmed: true
  - id: pharmacy-background
    domain: pharmacy
    claim: 药学本科；理解药物研发与临床证据分级，对「证据层级」有专业直觉
    evidence_refs: []
    status: author_stated
    can_offer: [跨领域类比]
    boundaries: 没做过临床、没做过药企研发
    confirmed: true
```

## 4. 接入点

所有读取点只消费 `confirmed_items()`；空画像时新增的 prompt 块渲染为 `(none)`，其余 prompt 文本与现状一致（回归保护）。

### 4.1 渲染函数（三处共用）

`src/finch/profile/render.py::render_user_practices(profile) -> str`，空画像返回 `"(none)"`：

```
- [agent-100-days] (sourced) agent engineering / teaching: 把 2024 至今 Agent 落地失败整理成 100 天路径
  can_offer: 方法卡 / 案例 / 对比 | boundaries: 没管过生产 Agent SLA | refs: https://github.com/flingjie/Agent-100-Days
- [pharmacy-background] (author_stated) pharmacy: 药学本科，熟悉临床证据分级
  can_offer: 跨领域类比 | boundaries: 没做过临床
```

### 4.2 机会评估 `opportunities/assess.py`

- `assess_opportunity(..., user_practices: str = "")` 新增参数；默认 `"(none)"`。
- `prompts/opportunity.md` 在 `## User context` 后新增 `## User real practices (confirmed, citeable)` 块。
- `why_me` 指令改为：优先把机会与某条 practice `id` 挂钩（写出 id）；无可挂钩条目时才允许写推断并标注 inference。
- 调用链统一传入：`discover.py` 的两个入口 → `discovery/daily.py`、`cli.py connect assess`、`from_url.py`。
- `discover.py` 的 skip 指纹已含 `user_context`；`user_practices` 同样并入指纹，使注入实践后旧 SKIP 缓存自然失效。

### 4.3 贡献制作 `opportunities/prepare.py`

- `write_contribution(..., practice_profile: PracticeProfile | None = None)`；`prepare_contribution` 透传。
- `prompts/prepare-contribution.md` 新增 `## User real practices` 块，并把「never invent personal experience」改为：
  只有此块中的条目可用第一人称写；每次引用必须带 `[practice-id]`；`author_stated` 条目可以陈述背景但不能引用链接；
  不写超出该条目 `boundaries` 的话；此块为空时维持现状（标 synthetic / 假设场景）。
- `cli.py` 的 `connect prepare` 加载 profile 并传入。

### 4.4 社区发现 `communities/service.py`

- `snapshot_context` 的 `practice_refs` 改为：有 practice profile（非空）时取所有 confirmed 条目的 `evidence_refs` 展平去重；
  否则回退 `settings.interests.practice_refs`。

## 5. CLI：`finch profile`

新 typer sub-app，与 `voice_app` 并列。

| 命令 | 行为 |
|---|---|
| `init [--repo owner/name]...` | 默认遍历 `settings.repositories`，可用 `--repo` 临时追加。对每个仓库 `gh api repos/{repo}/readme` 拉 README（只读），用现有 `critique` runner 按 `PracticeItem` schema 产出候选（`confirmed: false`）。已有文件时**只追加新 id，不覆盖已有条目**（幂等）。结束时打印：起草了哪些、哪些仓库跳过及原因，并提示「无公开资产的经历（药学 / 健身 / 阅读）请用 `finch profile add` 手工补」。 |
| `show [--json]` | 列出所有条目，标出 confirmed / 未确认、status。 |
| `confirm <id>` | 置 `confirmed: true`。 |
| `revoke <id>` | 置 `confirmed: false`。 |
| `add --id --domain --claim [--ref URL]... [--offer ...]... [--boundaries ...]` | 手工加一条；无 `--ref` 时 status 自动 `author_stated`；默认 `confirmed: false`，需再 `confirm`。 |

`init` 的 LLM 调用遵守子进程纪律：数组参数、每次调用超时、Pydantic 校验；README 内容作为数据而非指令
（prompt 中明示）。写文件经 `Workspace.atomic_write` 或等价原子写。

## 6. 配置变更（同一提交）

`finch.yaml`：

- `repositories` 追加 `flingjie/Agent-100-Days`、`flingjie/InvestAI`。
- `paths.practice_profile_path: practice-profile.yaml`（与默认一致，显式写出便于发现）。
- `interests.practice_refs` 加注释：「存在非空 practice-profile.yaml 时忽略此项」。

`settings.py`：`Paths.practice_profile_path: Path = Path("practice-profile.yaml")`。

## 7. 错误处理（fail-soft，与现有风格一致）

| 场景 | 行为 |
|---|---|
| profile 文件缺失 / 空 | 返回空画像，下游与今天一致 |
| YAML 损坏 | 返回空画像 + warning（stderr），不崩 |
| 单条校验失败（如 `sourced` 无 refs、重复 id） | 拒绝该条并报具体 id，其余条目正常加载 |
| `init` 某仓库 README 404 / `gh` 失败 / LLM 超时或格式非法 | 跳过该仓库，记录到输出，继续下一个；全部失败时不写空文件 |
| `confirm` / `revoke` 不存在的 id | 非零退出，列出可用 id |

## 8. 测试

- `tests/unit/test_practice_profile.py`：加载缺失 / 空 / 损坏文件；`sourced` 无 refs 校验；重复 id；`confirmed_items` 过滤；`render_user_practices` 输出快照；空画像返回 `(none)`。
- `tests/unit/test_opportunity_assess.py` 扩展：有 confirmed 条目时 prompt 含 practice 块与 id；空画像时 prompt 与改动前逐字一致。
- `tests/unit/test_prepare_contribution.py` 扩展：同上；`practice_profile=None` 与空画像等价。
- `tests/unit/test_profile_init.py`：fake runner + fake `gh` 输出；幂等追加（二次运行不覆盖已 `confirmed: true` 条目，不重复 id）；单仓库失败不中断。
- `tests/unit/test_community_context.py` 扩展：有 profile 时 `practice_refs` 来自 profile；无 profile 时回退配置。
- prompt contract test：`opportunity.md` / `prepare-contribution.md` 的占位符与 `.format()` 参数集合一致。

## 9. 验收

1. `finch profile init` 后 `practice-profile.yaml` 至少含 Agent-100-Days、InvestAI 两条候选；手工 `add` 药学 / 区块链出版 / 健身阅读条目并 `confirm`。
2. `finch connect daily` 的 `opportunity_assessments` 中不再出现「无用户经验 / 用户证据为空」类 skip_reason；至少一条 `why_me` 引用了 practice id。
3. `finch connect prepare --opportunity <id>` 生成的正文至少引用一个 `[practice-id]`，且不含超出该条目 `boundaries` 的第一人称陈述。
4. `uv run pytest` / `uv run ruff check .` / `uv run mypy src` 全绿。

## 10. 后续（不在本 spec）

- 第二阶段：`interests` / `sources` 查询词跨领域重定向（自学方法、药学、投资纪律、出版）。
- 配置债清理：移除 `finch.yaml` 中注明「当前主路径未消费」的字段或让代码消费它们。
- 最小表达路径：围绕「不擅长分享」设计每日一条回复、三句话上限的输出约束。
