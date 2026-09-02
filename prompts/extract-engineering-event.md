# Extract Engineering Event

You are given a group of related commits from one repository. Extract ONE engineering event.

## Rules

- Distinguish what the code PROVES from what is INFERRED.
- `problem` / `result` must be directly provable from the diff/tests/PR → `VERIFIED`; if only strongly implied, use `SUPPORTED`.
- `decision` (why / motivation) is almost always `INFERRED` unless a PR/issue explicitly states it.
- Never mark an inference as `VERIFIED`.
- If context is missing (e.g. was this a real bug or a proactive hardening?), list it in `missing_context`.
- `id` must be a stable slug, e.g. `evt_<repo-slug>_<short-topic>`.

## Input commits

{commits}

## Output

Respond with a JSON object matching the schema, with fields `id`, `repository`, `commits`, `problem`, `decision`, `result`, `missing_context`. `problem`/`decision`/`result` are objects `{"statement": str, "confidence": "VERIFIED|SUPPORTED|INFERRED|USER_CONFIRMED|UNKNOWN"}`.
