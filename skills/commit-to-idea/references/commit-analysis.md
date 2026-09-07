# Commit 分析：机械变化 vs 真实决策

从 commit 判断是否可提炼一个工程决策（问题 / 决策 / 结果），用于决定产出一个 `IdeaCandidate` 还是空列表。

## 三问

1. **问题**：这个变更解决了什么具体问题？（`problem`）
2. **决策**：作者做出了什么选择？为什么？（`decision`）
3. **结果**：这个选择带来了什么可验证的结果？（`result`）

三问都有明确答案（decision 非空且置信度非 UNKNOWN）→ 可提炼**一个** Idea。
任何一问空洞（"refactor"、"clean up"、"fix"）→ 不可提炼，空列表。

## 机械变化（直接跳过）

- 只改 lockfile（`package-lock.json` / `uv.lock` / `Cargo.lock` / `poetry.lock` …）。
- 纯格式化 / 命名重排（format / lint / prettier / ruff，无新增逻辑）。
- 纯 rename，无实质增删。
- 无文件变更。

判据：没有「读者值得知道」的新信息，只是仓库卫生。

## 真实决策（可提炼）

- 引入了新的架构选择（如「把 orchestrator 改成确定性图」）。
- 用一组明确代价换一个明确收益（tradeoff 可陈述）。
- 修复了一个有普遍意义的坑（不是改个拼写）。

判据：存在一个「别人可能也遇到、并能从你的选择中学到东西」的判断。

## 私有 / 敏感内容（不可发布）

- 私有仓库内容。
- 密钥 / token / 凭据。
- 不存在或无法追溯的 commit。

判据：任何一条命中即不可发布，空列表。

## 立场一律 proposed

commit 自动生成的立场是「建议」，不是授权结论。只有用户显式确认才是 `confirmed`
（见 `_shared/author-position.md`）。
