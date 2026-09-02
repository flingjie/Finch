# Finch

Finch 是一个证据驱动的 Builder 伙伴。它通过 `gh` 读取 GitHub Commit/PR/Issue/测试证据，通过 `opencli` 搜索与读取 Twitter/X 内容，将工程实践与公共技术讨论匹配，生成必须经人工审核的回复与原创内容。

当前状态：Phase 1（项目骨架与 Runtime）已完成。Graph 可确定性执行、失败恢复与重放；`finch diagnose` 可分别报告 `gh` 与 `opencli` 状态。

## 安装

```bash
uv sync
```

## 命令

```bash
uv run finch init      # 初始化 var/ 与数据库
uv run finch diagnose  # 探测 gh / opencli 可用性
```

详见 `docs/Finch-Codex-Development-Plan.md`。
