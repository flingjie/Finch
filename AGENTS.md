# AGENTS.md

供 AI 编码代理参考的项目约定。完整命令与目录见 `CLAUDE.md`；产品定义见 `docs/product-contract.md`。

## 核心原则

- Finch 是跨行业连接与灵感助理。公开表达是可选出口。PeerProfile 不是 CRM lead；Opportunity 不是商业机会。
- Evidence First：Commit → Engineering Event → Evidence Card → Draft，禁止 Commit 直接生成帖子。
- Codex 是智能节点，不是工作流 Runtime；状态、顺序、重试、幂等由确定性 Python 领域服务负责。
- 读取/写入权限分离：`gh` 仅读取；`opencli` 仅读取/搜索，禁止 twitter 写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。
- 发现单位是轻量 `Opportunity`（交流机会）。`finch connect daily` 首页只呈现 0–1 条首选；50 人分层在 `--view browse`（5 重点 / 15 摘要 / 30 浏览）。用户选定后由 `connect prepare --opportunity` 深度准备可审阅贡献（单次不超过 `deep_prepare_limit`，默认 5）。`connect prepare` 接受 `--reaction "<用户原话>"`；无任何反应时只准备澄清问题，正文第一人称只能来自 `[reaction]` 或 confirmed `[practice-id]`。LLM 不输出最终 `total`。
- 今日承诺面是 `finch connections today` / `finch people shortlist --today`，不在首页再造第二个人物池。
- 问题/workaround/使用反馈以 ConversationThread 笔记记录（必填 source_ref）；不建独立问题库或工具收款后台。礼貌兴趣 ≠ 试用成功 ≠ 付款。

## 交互约定（产品使用）

使用 Finch 帮用户发现、连接、讨论、表达时，先读 `skills/_shared/agent-presentation.md`
与 `skills/_shared/dialogue-policy.md`，再读目标 Skill。完成用户任务后做一次延伸点检查
（无价值就自然结束）。代码维护、测试日志不套用产品话术。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`
- 连接：`finch connect daily` / `prepare` / `assess`；`finch connections record` / `follow-up` / `today`
- 采集：`finch sources doctor` / `sync`（只读；twitter|reddit|github|v2ex|weixin|xiaohongshu）
- 仓库热度榜：`finch repos discover` / `list` / `export`（X 分享的 GitHub 项目；与 `repository_discovery` 用户自有仓库无关）
- 灵感与实验：`finch inspirations`、`finch collisions generate|weekly`、`finch experiments start`
- 表达分析：`finch article analyze`（表达任务/读者/有效性 + 写作风格七维，统一 ArticleReport）

## 目录

- `src/finch/opportunities/` 交流机会聚合（评估 / 状态机 / 贡献制作）
- `src/finch/discovery/` 每日发现编排
- `src/finch/sources/` 跨平台只读抓取（opencli gateway + RawArtifact）
- `src/finch/repos/` X 分享的 GitHub 仓库发现与热度榜（finch repos；与 repository_discovery 无关）
- `src/finch/engagement/` 发现快照 / InteractionRecord / 推荐反馈（旧评分管线已移除）
- `src/finch/connections/` 真实互动登记与机会跟进
- `src/finch/storage/` 文件 Workspace（YAML/Markdown/JSONL，原子写）
- `src/finch/github/` `src/finch/twitter/` `src/finch/reddit/` 只读 adapter
- `src/finch/conversations/` 对话线索与跟进
- `src/finch/dialogue/` 讨论摘要（`finch dialogue` save / search / show / forget）
- `src/finch/inspirations/` 灵感笔记
- `src/finch/collisions/` 跨领域碰撞与一周小实验
- `src/finch/communities/` 社区档案与反馈（无评分）
- `src/finch/ideas/` `src/finch/drafts/` 观点与草稿
- `src/finch/profile/` 用户已确认真实实践（practice-profile.yaml；finch profile）
