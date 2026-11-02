# 拆分 cli.py 设计（纯机械重构）

日期：2026-11-02
状态：设计已评审（brainstorming）
执行顺序：三项优化中第 1 个（cli 拆分 → 工件索引 → 问题事件流）

## 1. 问题

`src/finch/cli.py` 9270 行，占全仓 Python 行数约 69%。活跃命令（problem-discovery /
products / relationships / sources）与全部冻结命令混在一个文件，编辑定位困难，
import 全量加载。

## 2. 目标与边界

- **行为零变化**：命令名、参数、输出、hidden 状态、exit code 全部保持。
- 只移动代码，不改逻辑、不改措辞、不借机重构函数内部。
- `pyproject.toml` 入口 `finch = "finch.cli:app"` 不变。

## 3. 目标结构

```
src/finch/cli/
  __init__.py        # app 装配：创建根 typer，add_typer 挂载各模块；导出 `app`
  _shared.py         # load_settings/Workspace 初始化、跨域公共渲染帮助函数
  problem_discovery.py
  products.py
  relationships.py
  sources.py         # sources / twitter / repos / init / diagnose 等活跃读基础设施
  frozen.py          # 全部冻结命令（hidden=True）集中一个文件
```

- 渲染函数跟随唯一使用它的命令模块走；被多域共用的进 `_shared.py`。
- `frozen.py` 预计承载过半行数；活跃面每文件千行上下。
- 各模块定义自己的 `typer.Typer()` sub-app，由 `__init__.py` 统一 `add_typer`，
  名称与现有完全一致。

## 4. 测试与验收

- 迁移前保存 `finch --help`（含各 sub-app `--help`）输出快照；迁移后逐字节一致。
- 全量 `uv run pytest` 绿；测试只允许改 import 路径，不改断言。
- `uv run ruff check .`、`uv run mypy src` 与主干同水平。

## 5. 不做

- 不删除、不精简任何冻结命令。
- 不改命令行为或输出文案。
- 不调整 settings / Workspace 初始化逻辑。
