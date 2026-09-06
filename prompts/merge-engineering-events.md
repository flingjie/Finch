You are merging partial extractions of a single engineering change into one coherent event.

The change was too large to extract in one pass and was split into chunks. Each chunk produced a
partial EngineeringEvent below. Merge them into ONE event covering the whole change.

Repository: {repo}

Partial events (JSON):
{partials}

Return a single EngineeringEvent JSON object with:
- id: a short stable slug (e.g. "evt_<topic>")
- repository: {repo}
- commits: the union of all commits in the partials (full SHAs, deduplicated; no invented SHAs)
- problem / decision / result: one merged Claim each (statement + confidence)
- missing_context: deduplicated union
- topics: deduplicated union (max 5)

Rules:
- decision confidence must be INFERRED or lower (no PR/issue evidence at this stage).
- Do not introduce new facts or commits; stay faithful to the partials.
