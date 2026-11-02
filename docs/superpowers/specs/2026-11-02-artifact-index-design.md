# 工件存储索引设计（性能）

日期：2026-11-02
状态：设计已评审（brainstorming）
执行顺序：三项优化中第 2 个（cli 拆分 → 工件索引 → 问题事件流）

## 1. 问题

`var/artifacts` 已有 6000+ 个 YAML（35MB+），还在增长。`_collect_sources` 与
`finch sources` 诸多路径每次 `ArtifactRepository.list_all()` 全量 `yaml.safe_load`
后仅取最近 N（默认 40）条。`list_all()` 全仓调用点 100+，模式普遍。

## 2. 方案：追加式行索引 + 按需解析

索引是**派生物**，永远可从 YAML 全量重建；不引入第二真源。

### 2.1 索引文件

`var/artifacts/index.jsonl`，每行：

```json
{"artifact_id": "...", "published_at": "...", "retrieved_at": "...",
 "path": "artifacts/<file>.yaml", "content_fingerprint": "..."}
```

- `ArtifactRepository.upsert` 写 YAML 成功后 `Workspace.append_jsonl` 追加一行。
- 同一 `artifact_id` 多行时，读索引按行序后行覆盖前行（天然支持 upsert 语义）。

### 2.2 读路径

新方法 `ArtifactRepository.list_recent(*, cutoff: datetime | None, limit: int | None)
-> list[RawArtifact]`：

1. 读索引（单文件逐行 JSON）→ 按 id 去重（保留末行）→ 过滤 cutoff → 按时间排序
   → 截取 limit；
2. 仅对选中的 ≤N 条打开并解析 YAML；
3. 索引缺失或损坏 → 自动全量重建后重试（重建 = 遍历 YAML 写新索引，原子替换）。

`_collect_sources` 改用 `list_recent`。`list_all()` 保留，其余调用点不动。

### 2.3 维护入口

- `finch diagnose` 增加索引健康检查（行数 vs 文件数、可解析性）。
- 隐藏命令 `finch sources reindex-artifacts`：全量重建索引。

## 3. 验收

- 现有体量下 `discover`（replay 路径）/ `summary` / `candidates` 延迟较主干显著下降，
  记录实测数字（前后对比，不写估算）。
- 删除索引文件后首次调用自动重建，结果与全量路径一致（对照测试）。
- `upsert` 后 `list_recent` 立即可见新工件。
- 全量 pytest / ruff / mypy 绿。

## 4. 不做

- 不迁移 YAML → JSON/数据库；不改 RawArtifact 模型。
- 不给其他 100+ 处 `list_all()` 换索引（后续按需）。
- 不做跨目录通用索引框架（只做 artifacts 这一处，YAGNI）。
