# AGENTS.md

供 AI 编码代理参考的项目约定。完整命令与目录见 `CLAUDE.md`；产品定义见 `docs/product-contract.md`。

## 核心原则

- Finch 是 AI 产品发现 + 创业者连接 Copilot。产品与问题是并列的一等研究对象，二者通过证据或人工确认关联；创业者是主要连接对象。内容生产不是目的，也不服从连接。
- Evidence First：产品卡片、问题、方案都必须有来源工件；证据不足就留空或写「未知」，不编造。
- Evidence vs Assumption：区分「来源明确说出 / 由材料推导 / 未经验证的假设」三层；未引用证据的主张只能标假设。
- Codex 是智能节点，不是工作流 Runtime；状态、顺序、重试、幂等由确定性 Python 领域服务负责。
- 读取/写入权限分离：`gh` 与 `opencli` 仅读取；禁止写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。
- 确定性 totals：`score_total` / `score_dimensions` 只由代码计算，LLM 永不输出总分。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`
- 产品发现（本轮核心）：`finch products daily [--refresh] [--view home|browse] [--json] [--question ...]`、`finch products list`、`finch products show <id>`、`finch products feedback <id>`、`finch products explore <id>`
- 问题发现：`finch problem-discovery add|discover|list|show|summary|present|last-presented`
- 人物关系：`finch relationships import|add-person|add-event|record-contact|next|weekly`
- 采集：`finch sources doctor` / `sync`（只读；twitter|reddit|github|hackernews|v2ex|weixin|xiaohongshu）
- 仓库热度榜：`finch repos discover` / `list` / `export`

## 目录

- `src/finch/products/` 产品发现 / 产品卡片 / 方案分析 / 用户反馈研究
- `src/finch/problem_discovery/` 真实问题提取与问题收件箱
- `src/finch/relationships/` 人物与关系管理（任务理解 / 周度简报）
- `src/finch/sources/` 跨平台只读抓取（opencli gateway + RawArtifact）
- `src/finch/repos/` X 分享的 GitHub 仓库发现与热度榜
- `src/finch/storage/` 文件 Workspace（YAML/Markdown/JSONL，原子写）
- `src/finch/github/` `src/finch/twitter/` `src/finch/reddit/` `src/finch/hackernews/` 只读 adapter

旧使命命令（connect / peers / people / connections / conversations / practice 等）已冻结：隐藏于默认 CLI，代码与测试保留，不删除。
