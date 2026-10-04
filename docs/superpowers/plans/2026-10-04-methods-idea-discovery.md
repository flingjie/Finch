# Method-assisted idea discovery Implementation Plan

> Implemented against Cursor plan `methods_idea_discovery` (2026-10-04).
> Source brief: Downloads `Finch-Article-Methods-Idea-Discovery-Implementation-Plan.md`.

**Goal:** Evolve `expression_methods` and add an optional method-assisted path to idea-discovery after diverge/verify and before converge.

**Decisions:** No new `writing_methods` package; drafts/practice feedback alignment deferred; VoiceProfile untouched.

**Key surfaces:**

- `ExpressionMethod`: `purpose_tags`, `required_material`, richer `MethodSource`, `content_fingerprint()`, idempotent `save_as_new`
- `resolve_methods_for_discovery` + soft filter
- `IdeaDiverger.explore(..., methods=)` + `MethodAssistOutput` (generator version `2.1.0`)
- `finch ideas commit --method|--methods-from-report|--use-method-library`
- `ContentJob.method_id` / `method_use_as` / `method_fit_reason` / `method_version_hash`

**Invariant:** method provenance never enters fact `source_refs`.
