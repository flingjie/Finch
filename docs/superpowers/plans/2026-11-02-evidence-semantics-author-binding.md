# 证据含义与可信作者绑定（阶段 A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「引用已核对 / 主张被支持 / 问题已复现」三种结论在数据与呈现上分开；作者身份改由采集端绑定，LLM 草稿不再能发明身份；E2 收紧为「两件来源、两个来源绑定的外部 ID、宿主标第一手」。

**Architecture:** 全部改动在既有「LLM 草稿 → 确定性 build/admit 门禁 → 文件仓储」管线内：`models.py` 改枚举与字段（兼容读取旧值，不迁移磁盘），`discover.py` 的 `_collect_sources`/`build_problem` 增加来源作者映射与 `host_authored` 标志，`shadow.py` 的 validate-upsert 作为宿主路径，`repository.py`/`summary.py`/`cli.py` 调整默认过滤与呈现措辞。

**Tech Stack:** Python 3.12、Pydantic 2、typer、pytest。无新依赖。

**Spec:** `docs/superpowers/specs/2026-11-02-evidence-semantics-author-binding-design.md`

## Global Constraints

- Ruff：`E,F,I,B,UP`，line-length 100，py312（`pyproject.toml` 已配置）。
- 持久化只经 `Workspace`（YAML 文件），**不写迁移脚本、不改写已有磁盘数据**；旧值 `verified` 只在反序列化时映射。
- LLM 永不写总分；身份、等级封顶、计数全部由确定性代码执行。
- 中文 docstring，与所在文件风格一致。
- 每个任务结束运行该任务的测试；任务间用 git 提交分隔。
- 测试命令统一 `uv run pytest`；类型检查 `uv run mypy src`；lint `uv run ruff check .`。

---

### Task 1: 数据模型 — `Admission.SOURCE_CHECKED` 改名 + `claim_support` 字段 + 兼容读取

**Files:**
- Modify: `src/finch/problem_discovery/models.py`
- Test: `tests/unit/test_problem_discovery_models.py`（追加）

**Interfaces:**
- Produces: `Admission.SOURCE_CHECKED`（值 `"source_checked"`）；`ProblemEvidence.claim_support: Literal["supported","conflicting","unknown"]`、`ProblemEvidence.claim_support_reason: str`；`has_unresolved_conflict(problem: Problem) -> bool`。
- 后续任务（2、3、4）引用 `Admission.SOURCE_CHECKED` 与 `has_unresolved_conflict`。

- [ ] **Step 1: 写失败测试**

在 `tests/unit/test_problem_discovery_models.py` 追加：

```python
from finch.problem_discovery.models import (
    Admission,
    Problem,
    ProblemEvidence,
    has_unresolved_conflict,
)


def test_admission_source_checked_value():
    assert Admission.SOURCE_CHECKED.value == "source_checked"
    assert not hasattr(Admission, "VERIFIED")


def test_legacy_verified_admission_maps_to_source_checked():
    # 旧磁盘数据 admission: verified → 读取映射，不报错（验收案例 6）
    p = Problem.model_validate(
        {"problem_id": "prob_old", "title": "t", "admission": "verified"}
    )
    assert p.admission == Admission.SOURCE_CHECKED


def test_legacy_dict_without_admission_infers_source_checked():
    raw = {
        "problem_id": "prob_legacy",
        "title": "legacy",
        "evidence": [{"source_ref": "s1", "quote": "q", "claim": "c"}],
    }
    assert Problem.model_validate(raw).admission == Admission.SOURCE_CHECKED


def test_claim_support_defaults_unknown():
    e = ProblemEvidence(source_ref="s", quote="q")
    assert e.claim_support == "unknown"
    assert e.claim_support_reason == ""


def test_has_unresolved_conflict():
    clean = Problem(
        problem_id="p1",
        title="t",
        evidence=[ProblemEvidence(source_ref="s", quote="q")],
    )
    conflicted = Problem(
        problem_id="p2",
        title="t",
        evidence=[
            ProblemEvidence(source_ref="s", quote="q"),
            ProblemEvidence(
                source_ref="s2",
                quote="q2",
                claim_support="conflicting",
                claim_support_reason="摘录与主张相反",
            ),
        ],
    )
    assert not has_unresolved_conflict(clean)
    assert has_unresolved_conflict(conflicted)
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_problem_discovery_models.py -v`
Expected: FAIL（`SOURCE_CHECKED` 不存在 / `has_unresolved_conflict` 导入失败）

- [ ] **Step 3: 修改 `models.py`**

`Admission` 枚举（替换 `VERIFIED` 成员）：

```python
class Admission(StrEnum):
    """写入策略，与 ProblemStatus 并行：草稿 / 隔离 / 引用已核对 / 已接受。

    ``SOURCE_CHECKED`` 只表示「至少一条摘录可回溯到来源」；不表示主张被支持，
    更不表示问题已复现（复现只能由实验结果更新，属后续轮次）。
    """

    DRAFT = "draft"
    QUARANTINED = "quarantined"
    SOURCE_CHECKED = "source_checked"
    ACCEPTED = "accepted"
```

`ProblemEvidence` 追加字段（放在 `author_identity_confidence` 之后）：

```python
    # 主张支持情况：只能由宿主显式标注（build_problem 的 host_authored 路径），
    # 且必须附理由；代码不做语义推断。author_identity_confidence 保留兼容读取，
    # 不再参与门禁（身份可信 = 与采集端 author_identity 绑定成功）。
    claim_support: Literal["supported", "conflicting", "unknown"] = "unknown"
    claim_support_reason: str = ""
```

`Problem._legacy_admission` 验证器整体替换为（兼容 `verified` 旧值 + 原有推断逻辑）：

```python
    @model_validator(mode="before")
    @classmethod
    def _legacy_admission(cls, data: object) -> object:
        """旧载荷兼容：``verified`` 映射 ``source_checked``；缺 admission 按 evidence 推断。

        新鲜构造未传入 evidence（也未显式给 admission）时保持字段默认：
        quarantined，且 admission_reason 为空。只改内存表示，不回写磁盘。
        """
        if not isinstance(data, dict):
            return data
        if data.get("admission") == "verified":
            data = {**data, "admission": "source_checked"}
        if "admission" in data and data["admission"] not in (None, ""):
            return data
        if "evidence" not in data and "admission" not in data:
            return data
        evidence = data.get("evidence") or []
        data = {**data, "admission": "source_checked" if evidence else "quarantined"}
        if not data.get("admission_reason"):
            data["admission_reason"] = "source_checked" if evidence else "no_valid_evidence"
        return data
```

文件末尾（`ImportedDiscussion` 之前）追加模块级函数：

```python
def has_unresolved_conflict(problem: "Problem") -> bool:
    """任一证据被宿主标为与主张冲突 → 需复查，退出默认重点。"""
    return any(e.claim_support == "conflicting" for e in problem.evidence)
```

（放在 `Problem` 类定义之后即可，无需前向引用引号。）

- [ ] **Step 4: 全仓更新 `Admission.VERIFIED` 引用**

Run: `rg -l "Admission.VERIFIED|has_valid_evidence" src tests`

逐个替换 `Admission.VERIFIED` → `Admission.SOURCE_CHECKED`。涉及（以实际 rg 结果为准）：
- `src/finch/problem_discovery/discover.py`（`admit_problem`，Task 2 还会改 reason，本步先让代码可运行：`Admission.SOURCE_CHECKED` + reason 暂保持 `"has_valid_evidence"`，Task 2 一并改为 `"source_checked"`）
- `tests/unit/test_problem_discovery_admission.py`（断言改成 `Admission.SOURCE_CHECKED`；`test_legacy_dict_without_admission_infers_from_evidence` 的断言同步改；reason 断言在 Task 2 调整，本步若失败则按 Task 2 的最终值 `"source_checked"` 直接写死——见 Step 5 说明）

为避免两步改同一断言，本任务直接把 `admit_problem` 的 reason 一并改掉（见下），`discover.py` 中 `admit_problem` 整体替换为：

```python
def admit_problem(problem: Problem, *, force_draft: bool = False) -> Problem:
    """Write-policy gate: structure only (evidence count). Does not score claim semantics."""
    if force_draft:
        problem.admission = Admission.DRAFT
        problem.admission_reason = "host_draft"
        return problem
    if problem.evidence:
        problem.admission = Admission.SOURCE_CHECKED
        problem.admission_reason = "source_checked"
    else:
        problem.admission = Admission.QUARANTINED
        problem.admission_reason = "no_valid_evidence"
    return problem
```

同步更新 `tests/unit/test_problem_discovery_admission.py` 中的既有断言：
- `test_admit_problem_verifies_with_evidence`：`admission == Admission.SOURCE_CHECKED`、`admission_reason == "source_checked"`；函数名改为 `test_admit_problem_source_checks_with_evidence`。
- `test_admit_problem_quarantines_without_evidence` 中构造参数 `admission=Admission.VERIFIED` → `Admission.SOURCE_CHECKED`。
- `test_legacy_dict_without_admission_infers_from_evidence` → 断言 `Admission.SOURCE_CHECKED`。
- `test_validate_upsert_verifies_good_quote`、`test_validate_upsert_new_problem_unaffected_by_force`、`test_discover_problems_keeps_accepted_on_force_reextract`、`test_filter_default_hides_quarantined` 中的 `VERIFIED` / `"has_valid_evidence"` 同步替换（`filter_by_admission` 的默认串在 Task 4 改，本步测试里显式传 `"source_checked,accepted"`）。

- [ ] **Step 5: 运行相关测试**

Run: `uv run pytest tests/unit/test_problem_discovery_models.py tests/unit/test_problem_discovery_admission.py -v`
Expected: PASS（若 `filter_by_admission` 相关测试因默认串失败，确认测试已显式传 `"source_checked,accepted"`）

- [ ] **Step 6: Commit**

```bash
git add src/finch/problem_discovery/models.py src/finch/problem_discovery/discover.py tests/unit/test_problem_discovery_models.py tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): rename verified admission to source_checked, add claim_support"
```

---

### Task 2: 证据门禁 — 来源作者绑定 + 新 E2 规则 + 宿主标注门禁

**Files:**
- Modify: `src/finch/problem_discovery/discover.py`
- Modify: `prompts/problem-discovery.md`
- Test: `tests/unit/test_problem_discovery_discover.py`、`tests/unit/test_problem_discovery_admission.py`（追加/改写）

**Interfaces:**
- Consumes: Task 1 的 `Admission.SOURCE_CHECKED`。
- Produces: `_collect_sources(...) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]`（第三项：`source_ref → (external_id, handle)`，无结构化身份 = `("", "")`）；`build_problem(draft, *, source_texts, urls, authors=None, now, host_authored=False)`；`render_candidates_json(source_texts, urls, authors)`；`PROBLEM_VERSION = "problem-v3"`。Task 3、4 依赖这些签名。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_problem_discovery_discover.py` 追加（文件顶部已有 `_art` helper，构造 `RawArtifact` 时传 `author_identity=AuthorIdentity(platform="github", external_id=..., handle=...)`）：

```python
def _now2():
    return datetime(2026, 11, 2, tzinfo=UTC)


def test_author_binding_accepts_matching_source_identity():
    draft = ProblemDraft(
        title="t",
        evidence=[ProblemEvidenceDraft(source_ref="s", quote="alpha", author_id="Alice-1")],
    )
    p = build_problem(
        draft,
        source_texts={"s": "alpha here"},
        urls={},
        authors={"s": ("alice-1", "Alice")},
        now=_now2(),
    )
    assert p is not None
    assert p.evidence[0].author_id == "alice-1"
    assert p.evidence[0].author == "Alice"


def test_author_binding_clears_forged_identity():
    # 验收案例 2：伪造 author_id 与来源外部 ID 不符 → 身份清空，证据保留
    draft = ProblemDraft(
        title="t",
        evidence=[ProblemEvidenceDraft(source_ref="s", quote="alpha", author_id="mallory")],
    )
    p = build_problem(
        draft,
        source_texts={"s": "alpha here"},
        urls={},
        authors={"s": ("alice-1", "Alice")},
        now=_now2(),
    )
    assert p is not None
    assert len(p.evidence) == 1
    assert p.evidence[0].author_id == ""
    assert p.evidence[0].author == ""


def test_e2_requires_host_authored_first_hand():
    # 验收案例 4 / 4b：同样两条第一手证据，宿主路径 E2，LLM 路径封顶 E1
    def _draft():
        return ProblemDraft(
            title="t",
            evidence=[
                ProblemEvidenceDraft(
                    source_ref="s1",
                    quote="alpha",
                    evidence_level="e2",
                    experience="first_hand",
                    author_id="alice",
                ),
                ProblemEvidenceDraft(
                    source_ref="s2",
                    quote="beta",
                    evidence_level="e2",
                    experience="first_hand",
                    author_id="bob",
                ),
            ],
        )

    kw = dict(
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        authors={"s1": ("alice", "alice"), "s2": ("bob", "bob")},
        now=_now2(),
    )
    host = build_problem(_draft(), host_authored=True, **kw)
    llm = build_problem(_draft(), host_authored=False, **kw)
    assert {e.evidence_level for e in host.evidence} == {EvidenceLevel.E2}
    assert {e.evidence_level for e in llm.evidence} == {EvidenceLevel.E1}


def test_e2_same_external_id_counts_once():
    # 验收案例 3：同一外部 ID 多条来源算一个人 → E1
    draft = ProblemDraft(
        title="t",
        evidence=[
            ProblemEvidenceDraft(
                source_ref="s1", quote="alpha", experience="first_hand", author_id="alice"
            ),
            ProblemEvidenceDraft(
                source_ref="s2", quote="beta", experience="first_hand", author_id="alice"
            ),
        ],
    )
    p = build_problem(
        draft,
        source_texts={"s1": "alpha here", "s2": "beta there"},
        urls={},
        authors={"s1": ("alice", "alice"), "s2": ("alice", "alice")},
        now=_now2(),
        host_authored=True,
    )
    assert {e.evidence_level for e in p.evidence} == {EvidenceLevel.E1}


def test_claim_support_requires_host_and_reason():
    # 验收案例 1 / 7：宿主带理由 → 保留；LLM 路径或空理由 → 回落 unknown
    def _draft(reason: str):
        return ProblemDraft(
            title="t",
            evidence=[
                ProblemEvidenceDraft(
                    source_ref="s",
                    quote="alpha",
                    claim="速度变快",
                    claim_support="conflicting",
                    claim_support_reason=reason,
                )
            ],
        )

    kw = dict(source_texts={"s": "alpha here"}, urls={}, authors={}, now=_now2())
    host = build_problem(_draft("摘录说变慢，主张说变快"), host_authored=True, **kw)
    assert host.evidence[0].claim_support == "conflicting"
    no_reason = build_problem(_draft(""), host_authored=True, **kw)
    assert no_reason.evidence[0].claim_support == "unknown"
    llm = build_problem(_draft("理由"), host_authored=False, **kw)
    assert llm.evidence[0].claim_support == "unknown"


def test_collect_sources_returns_author_map(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    art = _art("github:issue:9", "body text")
    art.author_identity.external_id = "carol-7"
    art.author_identity.handle = "Carol"
    ArtifactRepository(ws).upsert(art)
    texts, urls, authors = _collect_sources(
        ArtifactRepository(ws), DiscussionRepository(ws)
    )
    assert authors["github:issue:9"] == ("carol-7", "Carol")
```

同时改写既有测试（语义已变）：
- `test_build_problem_copies_author_identity_fields` → 改名 `test_build_problem_binds_identity_from_source`：调用处传 `authors={"s": ("user-42", "u")}`，断言 `author_id == "user-42"`、`author == "u"`；删除对 `author_identity_confidence` 的断言。
- `test_build_problem_two_refs_allow_e2` → 改为传 `authors` 映射 + `host_authored=True` + 两条 `experience="first_hand"`，断言 E2（与新 `test_e2_requires_host_authored_first_hand` 互补，保留验证 build 内部封顶路径）。
- `tests/unit/test_problem_discovery_admission.py` 中 `test_n05_two_stated_author_ids_allow_e2` 删除（被上述 host/llm 对照取代）；`test_n05_same_author_id_two_refs_caps_e1`、`test_n05_empty_authors_two_refs_caps_e1` 保留但去掉 `author_identity_confidence` 参数（不传 `authors` → 无绑定 → E1，断言不变）。
- 其余 `build_problem(...)` 直接调用处无需传 `authors`（新参数带默认值 `None`）。

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_problem_discovery_discover.py tests/unit/test_problem_discovery_admission.py -v`
Expected: FAIL（`authors` / `host_authored` 参数不存在；`_collect_sources` 解包数量不符）

- [ ] **Step 3: 修改 `discover.py`**

版本号（门禁语义变化，作废旧指纹缓存）：

```python
PROBLEM_VERSION = "problem-v3"
```

`ProblemEvidenceDraft` 追加字段（`author_identity_confidence` 之后）：

```python
    claim_support: str = "unknown"
    claim_support_reason: str = ""
```

删除 `_coerce_identity_confidence` 与 `_independent_author_ids`，新增：

```python
def _bind_author(
    ev: ProblemEvidenceDraft, ref: str, authors: dict[str, tuple[str, str]]
) -> tuple[str, str]:
    """草稿 author_id 与来源外部 ID 一致（忽略大小写）→ 绑定；否则身份清空。

    身份只能来自采集边界（RawArtifact.author_identity）；LLM 无法发明身份。
    一条来源最多算一个人（评论者不另算，见规格 §3.2）。
    """
    external_id, handle = authors.get(ref, ("", ""))
    aid = ev.author_id.strip().casefold()
    if aid and external_id and aid == external_id.casefold():
        return external_id, handle
    return "", ""


def _independent_firsthand_authors(
    bound: list[tuple[ProblemEvidenceDraft, str, str, str]],
    *,
    host_authored: bool,
) -> set[str]:
    """E2 计数：来源绑定的外部 ID × 宿主标注 first_hand。LLM 自报不计数。"""
    if not host_authored:
        return set()
    ids: set[str] = set()
    for ev, _ref, external_id, _handle in bound:
        if external_id and _coerce_experience(ev.experience) is ExperienceLevel.FIRST_HAND:
            ids.add(external_id)
    return ids


def _coerce_claim_support(
    value: str, reason: str, *, host_authored: bool
) -> tuple[Literal["supported", "conflicting", "unknown"], str]:
    """主张支持只能由宿主显式标注且必须附理由；否则强制 unknown。"""
    v = (value or "unknown").strip().lower()
    if not host_authored or v not in ("supported", "conflicting") or not reason.strip():
        return "unknown", ""
    return cast(Literal["supported", "conflicting"], v), reason.strip()
```

`_collect_sources` 返回三元组（修改尾部，候选收集逻辑不变）：

```python
) -> tuple[dict[str, str], dict[str, str], dict[str, tuple[str, str]]]:
    """收集可抽取来源；返回 (正文, URL, 作者身份) 三个映射。

    作者身份来自采集边界：RawArtifact.author_identity → (external_id, handle)；
    手工导入讨论无结构化身份 → ("", "")。
    """
```

candidates 列表元素扩为 `(ts, ref, text, url, external_id, handle)`：artifact 分支追加
`art.author_identity.external_id, art.author_identity.handle`；discussion 分支追加 `"", ""`。
输出循环：

```python
    source_texts: dict[str, str] = {}
    urls: dict[str, str] = {}
    authors: dict[str, tuple[str, str]] = {}
    for _, ref, text, url, external_id, handle in candidates:
        if max_chars_per_source is not None and max_chars_per_source > 0:
            text = text[:max_chars_per_source]
        source_texts[ref] = text
        urls[ref] = url
        authors[ref] = (external_id, handle)
    return source_texts, urls, authors
```

排序 key 不变（仍按 `(ts, ref)`，元组后两位不参与）：`candidates.sort(key=lambda row: (-row[0].timestamp(), row[1]))`。

`render_candidates_json` 增加作者列（宿主/LLM 需看到来源作者才可能填对 `author_id`）：

```python
def render_candidates_json(
    source_texts: dict[str, str],
    urls: dict[str, str],
    authors: dict[str, tuple[str, str]] | None = None,
) -> str:
    amap = authors or {}
    rows = [
        {
            "source_ref": ref,
            "url": urls.get(ref, ""),
            "author_id": amap.get(ref, ("", ""))[0],
            "author": amap.get(ref, ("", ""))[1],
            "text": text,
        }
        for ref, text in sorted(source_texts.items())
    ]
    return json.dumps(rows, ensure_ascii=False, indent=2)
```

`build_problem` 整体替换：

```python
def build_problem(
    draft: ProblemDraft,
    *,
    source_texts: dict[str, str],
    urls: dict[str, str],
    authors: dict[str, tuple[str, str]] | None = None,
    now: datetime,
    host_authored: bool = False,
) -> Problem | None:
    """确定性把 draft 转为 Problem；空标题丢弃；evidence 必须回溯到来源，否则丢弃。

    URL 只取原始来源映射；空 quote 丢弃；作者身份只接受与采集端绑定一致的 ID
    （否则清空）；E2 需两个来源绑定的不同外部 ID 且宿主标 first_hand；
    claim_support 只接受宿主带理由的标注。无有效 evidence 时在 unknowns
    追加「未验证：无证据」，不把推测当事实。
    """
    if not draft.title.strip():
        return None

    amap = authors or {}
    normalized = {ref: _normalize(t) for ref, t in source_texts.items()}
    bound: list[tuple[ProblemEvidenceDraft, str, str, str]] = []
    for ev in draft.evidence:
        ref = ev.source_ref.strip()
        if ref not in source_texts:
            continue  # 来源不可回溯 → 丢弃
        quote = ev.quote.strip()
        if not quote:
            continue  # 空摘录不可核验 → 丢弃
        if _normalize(quote) not in normalized[ref]:
            continue  # 摘录无法回溯到来源 → 丢弃（防伪造）
        external_id, handle = _bind_author(ev, ref, amap)
        bound.append((ev, ref, external_id, handle))

    independent = len(
        _independent_firsthand_authors(bound, host_authored=host_authored)
    )

    evidence: list[ProblemEvidence] = []
    for ev, ref, external_id, handle in bound:
        raw_level = _coerce_level(ev.evidence_level)
        cap = _public_level_cap(
            is_marketing=ev.is_marketing,
            independent_authors=independent,
        )
        support, support_reason = _coerce_claim_support(
            ev.claim_support, ev.claim_support_reason, host_authored=host_authored
        )
        evidence.append(
            ProblemEvidence(
                source_ref=ref,
                url=urls.get(ref, ""),  # 忽略 LLM url，只用来源映射
                quote=ev.quote.strip(),
                claim=ev.claim.strip(),
                evidence_level=_cap_level(raw_level, cap),
                experience=_coerce_experience(ev.experience),
                is_marketing=ev.is_marketing,
                author=handle,
                author_id=external_id,
                claim_support=support,
                claim_support_reason=support_reason,
            )
        )

    unknowns = [u.strip() for u in draft.unknowns if u.strip()]
    if not evidence:
        marker = "未验证：无证据"
        if marker not in unknowns:
            unknowns.append(marker)
```

（函数尾部构造 `Problem(...)` 的部分保持现状不动。）

`_public_level_cap` 不变（参数名 `independent_authors` 继续使用）。

`discover_problems` 内两处调用更新：

```python
    source_texts, urls, authors = _collect_sources(...)
```

batch 循环内：

```python
        batch_authors = {ref: authors.get(ref, ("", "")) for ref in batch_refs}
        draft = assess_problems(
            runner, render_candidates_json(batch_texts, batch_urls, batch_authors)
        )
        ...
            problem = build_problem(
                pd,
                source_texts=batch_texts,
                urls=batch_urls,
                authors=batch_authors,
                now=clock,
                host_authored=False,
            )
```

顶部 import 增加 `cast` 已存在；确认 `Literal` 已导入。

- [ ] **Step 4: 更新抽取 prompt**

`prompts/problem-discovery.md`：
- 输入说明一行改为：`输入是 JSON 数组，每个元素是一条原始材料（`source_ref` + `url` + `author_id` + `author` + `text`）。`
- 示例 evidence 对象中 `"author": ""` 替换为：

```json
          "author": "",
          "author_id": ""
```

- 规则区追加一条：

```markdown
- `author_id` 只能照抄该 source_ref 输入行里的 `author_id`；输入为空或不确定就留空，
  不要猜测、不要从正文里的 @用户名 推断。
```

- [ ] **Step 5: 运行测试**

Run: `uv run pytest tests/unit/test_problem_discovery_discover.py tests/unit/test_problem_discovery_admission.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/finch/problem_discovery/discover.py prompts/problem-discovery.md tests/unit/test_problem_discovery_discover.py tests/unit/test_problem_discovery_admission.py
git commit -m "feat(problem-discovery): bind author identity at capture boundary, tighten E2, gate claim_support"
```

---

### Task 3: 宿主路径 — validate-upsert 作为 host_authored 入口

**Files:**
- Modify: `src/finch/problem_discovery/shadow.py`
- Test: `tests/unit/test_problem_discovery_shadow.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `_collect_sources` 三元组、`build_problem(..., authors, host_authored)`。
- Produces: `validate_upsert` 行为不变的签名；`export_candidates` 的 `sources` 行增加 `author_id` / `author` 键（宿主起草时需要）。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_problem_discovery_shadow.py` 追加（复用该文件既有 workspace/import helper 风格；若无，按 `test_problem_discovery_admission.py` 的 `_setup` 模式写）：

```python
from finch.problem_discovery.models import EvidenceLevel
from finch.sources.models import AuthorIdentity, RawArtifact, Source
from datetime import UTC, datetime


def _identity_art(ref: str, body: str, external_id: str, handle: str) -> RawArtifact:
    return RawArtifact(
        artifact_id=ref,
        source=Source.GITHUB,
        source_type="issue",
        source_id=ref,
        author_identity=AuthorIdentity(
            platform="github", external_id=external_id, handle=handle
        ),
        text=body,
        retrieved_at=datetime(2026, 11, 2, tzinfo=UTC),
    )


def test_validate_upsert_is_host_path_e2(tmp_path):
    # 验收案例 4：两名真实第一手报告者经 validate-upsert → E2
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ArtifactRepository(ws)
    repo.upsert(_identity_art("gh:1", "restore fails for me", "alice", "Alice"))
    repo.upsert(_identity_art("gh:2", "same restore failure here", "bob", "Bob"))
    drafts = {
        "problems": [
            {
                "title": "Restore failure",
                "evidence": [
                    {
                        "source_ref": "gh:1",
                        "quote": "restore fails for me",
                        "experience": "first_hand",
                        "author_id": "alice",
                        "evidence_level": "e2",
                    },
                    {
                        "source_ref": "gh:2",
                        "quote": "same restore failure here",
                        "experience": "first_hand",
                        "author_id": "bob",
                        "evidence_level": "e2",
                    },
                ],
            }
        ]
    }
    out = validate_upsert(
        drafts,
        artifact_repo=repo,
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
    )
    assert {e.evidence_level for e in out.problems[0].evidence} == {EvidenceLevel.E2}


def test_validate_upsert_accepts_host_claim_support(tmp_path):
    # 验收案例 1：宿主标 conflicting（带理由）经 validate-upsert 保留
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ArtifactRepository(ws)
    repo.upsert(_identity_art("gh:3", "it got slower after update", "carol", "Carol"))
    drafts = {
        "problems": [
            {
                "title": "Speed claim",
                "evidence": [
                    {
                        "source_ref": "gh:3",
                        "quote": "it got slower after update",
                        "claim": "更新后变快了",
                        "claim_support": "conflicting",
                        "claim_support_reason": "摘录说变慢，主张说变快",
                    }
                ],
            }
        ]
    }
    out = validate_upsert(
        drafts,
        artifact_repo=repo,
        discussion_repo=DiscussionRepository(ws),
        problem_repo=ProblemRepository(ws, shadow=True),
        run_repo=DiscoveryRunRepository(ws, shadow=True),
    )
    ev = out.problems[0].evidence[0]
    assert ev.claim_support == "conflicting"
    assert ev.claim_support_reason == "摘录说变慢，主张说变快"


def test_export_candidates_includes_author(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    ArtifactRepository(ws).upsert(_identity_art("gh:4", "body", "dave", "Dave"))
    export = export_candidates(
        artifact_repo=ArtifactRepository(ws), discussion_repo=DiscussionRepository(ws)
    )
    row = next(s for s in export.sources if s["id"] == "gh:4")
    assert row["author_id"] == "dave"
    assert row["author"] == "Dave"
```

（import 按该测试文件现有头部补齐：`validate_upsert`、`export_candidates`、`ArtifactRepository`、`DiscussionRepository`、`ProblemRepository`、`DiscoveryRunRepository`、`Workspace`。）

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_problem_discovery_shadow.py -v`
Expected: FAIL（解包数量 / E2 不成立 / sources 行缺 author 键）

- [ ] **Step 3: 修改 `shadow.py`**

`export_candidates`：

```python
    source_texts, urls, authors = _collect_sources(...)
    ...
    sources = [
        {
            "id": ref,
            "url": urls.get(ref, ""),
            "author_id": authors.get(ref, ("", ""))[0],
            "author": authors.get(ref, ("", ""))[1],
            "text": text,
        }
        for ref, text in sorted(source_texts.items())
    ]
```

`validate_upsert`：

```python
    source_texts, urls, authors = _collect_sources(...)
    ...
        problem = build_problem(
            draft,
            source_texts=source_texts,
            urls=urls,
            authors=authors,
            now=clock,
            host_authored=True,
        )
```

其余逻辑（ACCEPTED 保护、run 写入）不动。

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/unit/test_problem_discovery_shadow.py tests/unit/test_problem_discovery_shadow_experiment.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finch/problem_discovery/shadow.py tests/unit/test_problem_discovery_shadow.py
git commit -m "feat(problem-discovery): validate-upsert is the host-authored path; export author identity"
```

---

### Task 4: 默认过滤与呈现 — 冲突退出重点、措辞「引用已核对」、CLI 默认值

**Files:**
- Modify: `src/finch/problem_discovery/repository.py`
- Modify: `src/finch/problem_discovery/summary.py`
- Modify: `src/finch/cli.py`（`_render_problem` / `_render_problem_line` / problem-discovery 命令默认值与帮助文案）
- Test: `tests/unit/test_problem_discovery_repository.py`、`tests/unit/test_problem_discovery_summary.py`、`tests/unit/test_cli_problem_discovery.py`（追加/改写）

**Interfaces:**
- Consumes: Task 1 的 `has_unresolved_conflict`、`Admission.SOURCE_CHECKED`。
- Produces: `filter_by_admission(problems, admission="source_checked,accepted")`（legacy token `verified` 映射 source_checked）；`filter_default_focus(problems) -> list[Problem]`；`ProblemSummary.cards_needing_review: list[str]`。

- [ ] **Step 1: 写失败测试**

`tests/unit/test_problem_discovery_repository.py` 追加：

```python
from finch.problem_discovery.models import (
    Admission,
    Problem,
    ProblemEvidence,
)
from finch.problem_discovery.repository import filter_by_admission, filter_default_focus


def _p(pid: str, admission: Admission, conflicting: bool = False) -> Problem:
    ev = [ProblemEvidence(source_ref="s", quote="q")]
    if conflicting:
        ev.append(
            ProblemEvidence(
                source_ref="s2",
                quote="q2",
                claim_support="conflicting",
                claim_support_reason="r",
            )
        )
    return Problem(problem_id=pid, title=pid, admission=admission, evidence=ev)


def test_filter_default_uses_source_checked():
    items = [
        _p("a", Admission.SOURCE_CHECKED),
        _p("b", Admission.QUARANTINED),
        _p("c", Admission.ACCEPTED),
    ]
    assert [p.problem_id for p in filter_by_admission(items)] == ["a", "c"]


def test_filter_legacy_verified_token_still_works():
    items = [_p("a", Admission.SOURCE_CHECKED)]
    assert len(filter_by_admission(items, "verified,accepted")) == 1


def test_default_focus_excludes_conflicting():
    items = [
        _p("clean", Admission.SOURCE_CHECKED),
        _p("conflicted", Admission.SOURCE_CHECKED, conflicting=True),
        _p("accepted_conflicted", Admission.ACCEPTED, conflicting=True),
    ]
    assert [p.problem_id for p in filter_default_focus(items)] == ["clean"]
```

`tests/unit/test_problem_discovery_summary.py` 追加（复用该文件既有 workspace/upsert helper 风格）：

```python
def test_summary_reports_cards_needing_review(tmp_path):
    ws = Workspace(tmp_path)
    ws.ensure()
    repo = ProblemRepository(ws)
    repo.upsert(
        Problem(
            problem_id="prob_conf",
            title="conflicted",
            admission=Admission.SOURCE_CHECKED,
            evidence=[
                ProblemEvidence(
                    source_ref="s",
                    quote="q",
                    claim_support="conflicting",
                    claim_support_reason="r",
                )
            ],
        )
    )
    summary = build_summary(
        problem_repo=repo,
        artifact_repo=ArtifactRepository(ws),
        discussion_repo=DiscussionRepository(ws),
        run_repo=DiscoveryRunRepository(ws),
    )
    assert summary.cards_needing_review == ["prob_conf"]
    assert summary.total_cards == 0  # 冲突卡退出默认重点计数
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run pytest tests/unit/test_problem_discovery_repository.py tests/unit/test_problem_discovery_summary.py -v`
Expected: FAIL

- [ ] **Step 3: 修改 `repository.py`**

```python
def filter_by_admission(
    problems: list[Problem], admission: str = "source_checked,accepted"
) -> list[Problem]:
    """按写入策略过滤收件箱；``all`` / ``*`` / 空串保留全部（含隔离）。

    旧 token ``verified`` 兼容映射为 ``source_checked``。
    """
    key = admission.strip().lower()
    if key in {"", "all", "*"}:
        return list(problems)
    allowed = {
        "source_checked" if part.strip() == "verified" else part.strip()
        for part in key.split(",")
        if part.strip()
    }
    return [p for p in problems if p.admission.value in allowed]


def filter_default_focus(problems: list[Problem]) -> list[Problem]:
    """默认重点候选：source_checked|accepted 且无未解决的主张冲突（规格 §5）。"""
    return [
        p
        for p in filter_by_admission(problems)
        if not has_unresolved_conflict(p)
    ]
```

顶部 import 增加 `has_unresolved_conflict`。

- [ ] **Step 4: 修改 `summary.py`**

- `build_summary` 参数默认 `admission: str = "source_checked,accepted"`。
- `ProblemSummary` 新增字段 `cards_needing_review: list[str] = Field(default_factory=list)`（注释：被标 claim 冲突、退出默认重点的卡）。
- 过滤后拆分：

```python
    admitted = filter_by_admission(problem_repo.list_all(), admission)
    needs_review = [p.problem_id for p in admitted if has_unresolved_conflict(p)]
    problems = [p for p in admitted if p.problem_id not in set(needs_review)]
```

其余统计继续用 `problems`；返回时传 `cards_needing_review=needs_review`。
`_collect_sources` 解包改三元：`_, urls, _ = _collect_sources(artifact_repo, discussion_repo)`。
import 增加 `has_unresolved_conflict`。

- [ ] **Step 5: 修改 `cli.py`**

- `problem_discovery_list` / `problem_discovery_summary` 的 `--admission` 默认值与帮助：

```python
    admission: str = typer.Option(
        "source_checked,accepted",
        "--admission",
        help="Filter by admission (default source_checked,accepted; legacy verified accepted; all for quarantine)",
    ),
```

docstring 同步改为「默认 source_checked,accepted」。
- `problem_discovery_list` 主体：过滤后用 `filter_default_focus` 拆分显示，隔离/冲突单独说明：

```python
    all_problems = DiscoveredProblemRepository(ws).list_all()
    problems = filter_by_admission(all_problems, admission)
    conflicted = [p for p in problems if has_unresolved_conflict(p)]
    problems = [p for p in problems if not has_unresolved_conflict(p)]
```

渲染尾部追加：

```python
    if conflicted:
        typer.echo(f"待复查（主张与摘录冲突，不计入重点）：{len(conflicted)} 张")
        for p in conflicted:
            typer.echo(f"  - {p.title}  [{p.problem_id}]")
```

import 行追加 `has_unresolved_conflict`（从 `.problem_discovery.models`）。
- `_render_problem`：标题行后增加写入状态行；证据循环内显示宿主标注：

```python
_ADMISSION_LABEL = {
    "source_checked": "引用已核对（不代表主张成立或已复现）",
    "accepted": "已接受",
    "quarantined": "隔离（无有效证据）",
    "draft": "草稿",
}
```

（模块级常量，放在 `_render_problem` 定义前。）函数内标题行之后：

```python
    typer.echo(f"  写入状态：{_ADMISSION_LABEL.get(p.admission.value, p.admission.value)}")
```

证据循环内 `claim` 行之后：

```python
        if e.claim_support != "unknown":
            label = "支持" if e.claim_support == "supported" else "与摘录冲突"
            typer.echo(f"      主张核对：{label}（{e.claim_support_reason}）")
        if e.author:
            typer.echo(f"      作者：{e.author}")
```

- `problem_discovery_summary` 文本输出追加：

```python
    if summary.cards_needing_review:
        typer.echo(f"  待复查卡片：{len(summary.cards_needing_review)} 张")
```

- `problem_discovery_admit` docstring 中 `verified` 字样改为 `source_checked`（行为不变）。
- Run: `rg -n "verified" src/finch/cli.py src/finch/problem_discovery/` 清点残留，仅保留兼容映射处。

- [ ] **Step 6: 更新 CLI 测试并运行**

`tests/unit/test_cli_problem_discovery.py`：按既有断言把 `verified` 期望值改为 `source_checked`；新增一条覆盖验收案例 5（accepted 无证据卡仍明示缺证据）：

```python
def test_show_accepted_without_evidence_displays_gap(tmp_path, ...):
    # 构造 admission=ACCEPTED、evidence=[] 的卡片写入 workspace，
    # 调用 show，断言输出含「证据（0 条）」与「已接受」。
    # 具体 runner/workspace 夹具复用该文件现有模式。
```

（按该文件实际夹具写完整测试体，断言输出同时含 `已接受` 与 `证据（0 条）`。）

Run: `uv run pytest tests/unit/test_problem_discovery_repository.py tests/unit/test_problem_discovery_summary.py tests/unit/test_cli_problem_discovery.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/finch/problem_discovery/repository.py src/finch/problem_discovery/summary.py src/finch/cli.py tests/unit/test_problem_discovery_repository.py tests/unit/test_problem_discovery_summary.py tests/unit/test_cli_problem_discovery.py
git commit -m "feat(problem-discovery): conflict-aware default focus, source_checked wording and defaults"
```

---

### Task 5: 文档定位同步

**Files:**
- Modify: `README.md`、`docs/product-contract.md`、`AGENTS.md`、`CLAUDE.md`
- Modify: `.codex/skills/problem-discovery/SKILL.md`（如含「已核实 / verified」措辞则同步）

**Interfaces:** 无代码接口；验收靠 grep。

- [ ] **Step 1: 统一定位表述**

四份文档中关于使命/闭环的段落，统一为以下要点（措辞融入各文档现有行文，不整段重写）：
- Finch 帮助用户**从真实使用障碍中发现值得自己解决的问题**；问题是默认入口与核心研究对象。
- 产品是已有方案和替代方案；人物是问题当事人、验证伙伴和潜在合作者。
- 产品发现、反馈研究与关系能力按问题研究需要调用，不要求先经过产品发现或连接创业者。
- 运行目标：每周至少推进一个有证据支持的投入或放弃决定；分别记录实际验证、判断变化和真实反馈，不汇总总分；不承诺每周必有产出。

具体落点：
- `docs/product-contract.md`：首段定位句与「核心闭环」入口描述；北极星指标下补一行问题推进目标。
- `CLAUDE.md`：`## Project` 段首句与 shipped scope 描述。
- `AGENTS.md`：「核心原则」第一条。
- `README.md`：项目简介段。

- [ ] **Step 2: 证据措辞同步**

- 四份文档及 `.codex/skills/problem-discovery/SKILL.md` 中出现的 admission `verified` / 「已核实」改为 `source_checked` / 「引用已核对」；并在 problem-discovery 相关段落加一句三层结论的区分（引用已核对 ≠ 主张被支持 ≠ 已复现；复现只能由实验结果更新，属后续轮次）。
- `CLAUDE.md` 的 problem-discovery CLI 行补 `admit`（已存在命令）与默认过滤 `source_checked,accepted` 的说明（一句话）。

- [ ] **Step 3: 验证**

Run: `rg -n "已核实" README.md docs/product-contract.md AGENTS.md CLAUDE.md .codex/skills/problem-discovery/`
Expected: 无输出（或仅剩明确标注为旧值兼容说明的行）

Run: `rg -n "verified,accepted" src docs README.md AGENTS.md CLAUDE.md`
Expected: 仅测试/兼容映射处出现。

- [ ] **Step 4: Commit**

```bash
git add README.md docs/product-contract.md AGENTS.md CLAUDE.md .codex/skills/problem-discovery/SKILL.md
git commit -m "docs: problem-first positioning and source_checked evidence wording"
```

---

### Task 6: 全量回归与质量门

**Files:** 无新改动（只跑检查，修复发现的问题）

- [ ] **Step 1: 全量测试**

Run: `uv run pytest`
Expected: PASS。若 products / sources 等共享面有失败，逐个修复（本轮未改共享模型，预期只有 problem_discovery 面受影响）。

- [ ] **Step 2: Lint 与类型**

Run: `uv run ruff check . && uv run mypy src`
Expected: 均无错误。常见修复点：`_coerce_claim_support` 的 `cast` 与 `Literal` 导入；`_collect_sources` 返回类型注解。

- [ ] **Step 3: 冒烟（真实 workspace 只读确认）**

Run: `uv run finch problem-discovery list --admission all | head -20`
Expected: 旧数据正常读取（`verified` 卡显示为 source_checked 语义），无 traceback。

- [ ] **Step 4: Commit（如有修复）**

```bash
git add -A
git commit -m "test(problem-discovery): full-suite fixes for source_checked semantics"
```

---

## Self-Review 结果

- **Spec coverage**：§2 枚举与字段 → Task 1；§3 身份绑定与 E2（含 host_authored）→ Task 2；§4 两路径一致 → Task 2+3；§5 查询呈现 → Task 4；§6 文档 → Task 5；§7 验收案例 1→T2/T4、2→T2、3→T2、4→T3、4b→T2、5→T4、6→T1、7→T2、8→既有 shadow 测试回归（T6 全量）；§8 写入者原则体现在 T2 门禁实现。
- **Placeholder**：Task 4 Step 6 的 CLI 测试体依赖该文件现有夹具，已注明按现有模式补全断言（`已接受` + `证据（0 条）`），无 TBD。
- **类型一致性**：`authors: dict[str, tuple[str, str]]` 贯穿 `_collect_sources` / `build_problem` / `render_candidates_json` / `export_candidates`；`claim_support` 的 Literal 值三处一致（models / draft coercion / 测试）。
