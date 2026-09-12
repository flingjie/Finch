# AGENTS.md

供 AI 编码代理参考的项目约定。

## 核心原则

- Evidence First：Commit → Engineering Event → Evidence Card → Draft，禁止 Commit 直接生成帖子。
- Codex 是智能节点，不是工作流 Runtime；状态、顺序、重试、幂等由确定性 Python 领域服务负责。
- 读取/写入权限分离：`gh` 仅读取；`opencli` 仅读取/搜索，禁止 twitter 写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。
- 发现单位是轻量 `Opportunity`；`InteractionProposal` 仅在用户选中后深度准备。LLM 不输出最终 `total`。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`

## 目录

- `src/finch/engagement/` 发现 / 机会 / 提案 / 门禁
- `src/finch/storage/` 文件 Workspace（YAML/Markdown/JSONL，原子写）
- `src/finch/github/` `src/finch/twitter/` 只读 adapter
- `src/finch/conversations/` 对话线索与跟进
- `src/finch/ideas/` `src/finch/drafts/` 观点与草稿
