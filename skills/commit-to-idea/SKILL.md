---
name: commit-to-idea
description: >
  从 GitHub Commit、PR、Issue 与测试变化中提炼可发布的工程 Idea（IdeaCandidate）。判断一处
  变更是否承载真实工程决策（问题/决策/结果），还是机械变化或私有内容；有明确决策才产出一个
  候选，否则返回空列表。用于「把这个仓库最近的提交变成可写的内容想法」类请求。
---

# commit-to-idea

从 Commit / PR / Issue / 测试变化中提炼 Idea。职责单一：判断变更里有没有「读者值得知道的工程决策」，有就产出**一个** `IdeaCandidate`，没有就产出空列表。

本 Skill 只调用 Finch CLI（`finch ideas commit`），不复制业务逻辑、不直接改数据库、不猜测状态。

## 职责

- 输入：一组 Commit / PR / Issue / 测试变化（由 `finch ideas commit` 从 GitHub 读取并预处理）。
- 输出：`IdeaCandidate` 列表（契约见 `_shared/idea-contract.md`）。
- 机械变化（lockfile、格式化、纯 rename、无文件变更）→ 空列表。
- 私有仓库 / 私密内容 / 密钥 → 不可发布，空列表。
- 没有明确工程决策（decision 为空或 UNKNOWN）→ 空列表。

## 执行

用 `finch ideas commit [--repo REPO] [--since 7d] [--json]`：

1. 读取最近 Commit（`load_commit_details`）。
2. 过滤机械变化（`CommitReader.filter_noise`）。
3. 提取 EngineeringEvent（`Extractor.extract`）。
4. 安全扫描（私有内容 / 密钥 / 不存在 commit）。
5. 有明确决策的事件 → `IdeaCandidate`；其余跳过。
6. `IdeaService.create_candidate` 幂等落库为 `ContentJob`（PROPOSED）。

## 产出契约（`IdeaCandidate`）

见 `_shared/idea-contract.md`，要点：

- `core_point` 只有一个中心主张；多个主张拆成多个候选。
- `source_refs` 可追溯（type + ref + summary，能反查到具体 commit）。
- `author_position.status` 一律 `proposed`（自动生成，未获授权）。
- `boundaries.known/inferred/unknown` 从事件置信度映射，传递到 Draft 校验。
- `origin="commit"`；`generator.skill="commit-to-idea"`。

## 强制规则

- 证据优先：`Commit → EngineeringEvent → EvidenceCard → Draft`；没有 Evidence Card 不生成内容（见 `_shared/evidence-policy.md`）。
- 不把推断写成已验证事实（见 `_shared/evidence-policy.md`）。
- 自动生成立场一律 `proposed`；只有用户确认才是 `confirmed`（见 `_shared/author-position.md`）。
- 不公开私有仓库内容；遇到敏感信息（密钥 / token / 私有内容）立即停止。
- 不自动发布；分数由代码算，LLM 输出不携带 total。

## 判断一个 Commit 是否有可提炼的决策

见 `references/commit-analysis.md`。

## 参考

- `references/commit-analysis.md` — 机械变化 vs 真实决策的判据。
- `_shared/idea-contract.md` — IdeaCandidate 契约。
- `_shared/evidence-policy.md` — 证据优先与边界。
- `_shared/author-position.md` — proposed vs confirmed。
