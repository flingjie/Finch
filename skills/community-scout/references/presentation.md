# 社区行动卡呈现配方

形状真源与命令映射。共享呈现原则见 `_shared/agent-presentation.md`（结论 → 决策卡 → 操作）。

## 周报（3 张卡）

每张卡固定回答七个问题，全部可追溯到公开证据：

1. **这是什么社区**（name + platforms）
2. **为什么现在适合你**（why_fit）
3. **近期公开证据**（recent_evidence，带 url）
4. **值得关注的核心 Builder**（people）
5. **当前可切入的问题**（entry_point.discussion + suggested_angle）
6. **你能贡献什么**（first_contribution.type + proposal）
7. **参与成本与风险**（risks）

不足 3 个合格社区时如实显示覆盖缺口，不用弱证据凑满。

## 用户回复 → CLI 映射

| 用户说 | 记录命令 |
|---|---|
| 不感兴趣 | `finch community feedback <id> --result ignored` |
| 以后可能参与 | `finch community feedback <id> --result saved` |
| 已加入 / 开始关注 | `finch community feedback <id> --result joined` |
| 完成第一次公开互动 | `finch community feedback <id> --result interacted` |
| 和成员第二次交流 | `finch community feedback <id> --result repeated` |
| 提交了代码/案例/工具 | `finch community feedback <id> --result contributed` |
