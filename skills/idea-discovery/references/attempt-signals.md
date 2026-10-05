# attempt-signals：实践尝试是否够格成为 Idea 的判据

`finch ideas create --attempt <id>` 读一条 `PracticeAttempt`（问题/尝试/观察/未知/下一步）。
判据与 `commit-signals.md` 同风格：**有没有真实观察 + 一个非显然的未知**。

**够格（值得提炼）**

- 观察推翻了之前的假设（原以为 X，实际发现 Y）。
- 一次失败 + 修复，或一个方案的前后比较。
- 尚未解决、适合请教同行的问题（未知具体、可被他人补充）。
- 有真实观察支撑的「这个判断只在某条件下成立」。

**不够格（→ 空 / 不提炼）**

- 机械操作、无观察、纯情绪或新闻。
- 只有结论没有观察；或「未知」泛泛到无法被同行回应。

**证据映射（不把读到的方法写成亲历）**

- `observation` → `facts`（`evidence_status=observed`）。
- `unknown` + `next_step` → `boundaries.unknown`，绝不写成已完成。
- 你的判断 → `interpretation`，与 `facts` 分列。
- 有「原先以为 → 实际发现」对照时，`content_type=judgment_shift`。
