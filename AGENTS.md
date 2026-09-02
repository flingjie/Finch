# AGENTS.md

供 AI 编码代理参考的项目约定。

## 核心原则

- Evidence First：Commit → Engineering Event → Evidence Card → Draft，禁止 Commit 直接生成帖子。
- Codex 是智能节点，不是工作流 Runtime；Graph 状态、顺序、重试、幂等由确定性 Python Runtime 负责。
- 读取/写入权限分离：`gh` 仅读取；`opencli` 仅读取/搜索，禁止 twitter 写命令。
- 子进程参数用数组传递；每次调用设超时；输出强制 JSON 并 Pydantic 校验。

## 命令

- 测试：`uv run pytest`
- 代码质量：`uv run ruff check .`、`uv run mypy src`
- 运行：`uv run finch <command>`

## 目录

- `src/finch/graph/` 确定性 Runtime；`src/finch/storage/` SQLite；`src/finch/github/` `src/finch/twitter/` 只读 adapter。
