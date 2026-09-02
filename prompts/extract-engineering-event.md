# Extract Engineering Event

You are given a group of related commits from one repository. Extract ONE engineering event.

## Rules

- Distinguish what the code PROVES from what is INFERRED.
- `problem` / `result`: use `VERIFIED` ONLY if the diff/patch shown directly proves it. If you only see commit messages and file paths (no diff, or a truncated diff), use `SUPPORTED` — never `VERIFIED` from metadata alone.
- `decision` (why / motivation): ALWAYS `INFERRED` (or `UNKNOWN`) — the diff cannot prove motivation. Never use `VERIFIED`/`SUPPORTED` for `decision`.
- Never mark an inference as `VERIFIED`.
- If context is missing (e.g. was this a real bug or a proactive hardening?), list it in `missing_context`.
- `id` must be a stable slug, e.g. `evt_<repo-slug>_<short-topic>`.

## Input commits

{commits}

## Output

Respond with a JSON object matching the schema, with fields `id`, `repository`, `commits`, `problem`, `decision`, `result`, `missing_context`. `problem`/`decision`/`result` are objects `{"statement": str, "confidence": "VERIFIED|SUPPORTED|INFERRED|USER_CONFIRMED|UNKNOWN"}`.
