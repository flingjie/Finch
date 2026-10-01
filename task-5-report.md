# Task 5 report

## fix(opportunities): ignore (none) marker in opportunity fingerprints

**Issue:** `render_user_practices` returns `"(none)"` for empty profiles; fingerprint code treated it as material, shifting `opp_*` ids vs pre–practice-profile behavior.

**Change:** `opportunity_context_fingerprint` (`discover.py`) and URL assess fingerprint (`from_url.py`) skip `user_practices` when empty or `NONE_MARKER`. Test: `test_context_fingerprint_none_marker_matches_empty`.
