# HappyPet Pipeline Audit Findings
**Date:** 2026-05-19
**Auditor:** Maeve (mobile session)
**Method:** Full file reads — generate_posts.py (50KB truncated), post_pins.py, push_pins_to_sheets.py, generate.yml, publish.yml, pin.yml + skills: vibe-code-auditor, python-pro, bash-pro, maeve-github-devops, engineering-craft, jekyll-affiliate, avoid-ai-writing, beautiful-prose

---

## CRITICAL

### C1 — Fact-check truncates to 1500 chars (P3)
**File:** generate_posts.py, `fact_check_alternatives()`
**Code:** `content_fc = content[:1500] if len(content) > 1500 else content`
**Impact:** 7000-char articles fact-checked at 21%. Fabrications in sections 2-7 are never caught. Both primary and fallback paths receive the truncated prompt.
**Fix:** Check full article. Chunk at 3500-char overlapping windows if model context requires it. Never truncate below 60% of article length.

### C2 — Generator exits 0 on total provider failure (P4)
**File:** generate_posts.py `main()`, generate.yml
**Impact:** GHA reports `conclusion: success` even when 0 articles were generated. Every "silent failure" run you've ever seen is this.
**Fix:** Write `GENERATION_RESULT.json` at script end with `articles_generated`, `articles_held`, `articles_failed`. GHA step reads it and `exit 1` if both generated and held are 0.

### C3 — Fact-check routes OpenRouter model to Groq endpoint (NEW — not in problem map)
**File:** generate_posts.py, `fact_check_alternatives()`
**Code:** `OR_FACTCHECK_MODEL = "openai/gpt-oss-20b:free"` sent to `GROQ_URL` with `groq_key` auth.
**Impact:** Groq API rejects the unknown model. Primary fact-check fails on every roundup article. Gemini fallback catches it silently. No error surfaces in logs beyond "Fact-check primary failed."
**Fix:** Route `OR_FACTCHECK_MODEL` to `OPENROUTER_URL` with `OPENROUTER_API_KEY`. Groq models (if re-enabled) go to `GROQ_URL`. Never mix endpoint and model families.

### C4 — GHA action versions do not exist
**File:** generate.yml, publish.yml, pin.yml
**Code:** `actions/checkout@v6`, `actions/setup-python@v6`, `actions/upload-artifact@v7`
**Impact:** These versions don't exist. Latest stable: checkout@v4, setup-python@v5, upload-artifact@v4. GHA resolves unknown versions unpredictably — may pin to latest or fail.
**Fix:** Pin all actions to verified current versions.

---

## HIGH

### H1 — Reviewer rubric catches 6 of 21 AI patterns (P2)
**File:** generate_posts.py, `make_review_prompt()`
**Impact:** Significance inflation, copula avoidance, superficial -ing analyses, synonym cycling, inline-header bullet lists, rule-of-three overuse, title case headings all pass review. `em_dash_count` is logged but does not gate pass/fail.
**Fix:** Full replacement of `make_review_prompt()` built from avoid-ai-writing 43-entry catalog and beautiful-prose style contract. Em dash count > 0 must gate fail.

### H2 — P8 utcnow() deprecation
**File:** post_pins.py: `fired_sentinel.write_text(_dt.datetime.utcnow().isoformat())`
**Impact:** DeprecationWarning in Python 3.12, will raise in future versions.
**Fix:** Global replace across all scripts: `datetime.datetime.utcnow()` → `datetime.datetime.now(datetime.timezone.utc)`. Confirm `import datetime` (not `from datetime import datetime`) or adjust accordingly.

### H3 — No S2→S3 automatic trigger (P7, queue item #3)
**File:** publish.yml, pin.yml
**Impact:** `pin.yml` is `workflow_dispatch`-only. `publish.yml` writes `.pending-slugs` but nothing triggers `pin.yml` automatically. Pins never fire without manual intervention. This is structural.
**Fix:** Add `workflow_run` trigger to `pin.yml` on `Stage 2 — Publish Articles` completion, or implement `PENDING_DRAFTS.json` model where S2 writes the file and S3 reads it.

### H4 — Rewrite attempt 2 references Groq (blocked from GHA)
**File:** generate_posts.py, `review_and_rewrite()`, attempt-2 branch
**Code:** Logs "REWRITING via Groq llama-3.3-70b" then bails if `GROQ_API_KEY` missing or CF-blocked.
**Impact:** Attempt-2 rewrites always fail silently on GHA. Articles that fail review twice are held without a second rewrite attempt ever executing.
**Fix:** Replace Groq rewrite path with Gemini direct API (already exists for attempt-1) or OpenRouter fallback.

### H5 — P5 pin image URL semantics not enforced by named contract
**File:** post_pins.py `ensure_cache_bust()`, push_pins_to_sheets.py line ~73, generate_posts.py `build_pin_image_url()`
**Impact:** The rule (IFTTT = bare URL, queue/sheets = ?v= URL) is correct but enforced by developer memory and a late-append patch in push_pins_to_sheets.py. Wrong URL type in wrong context is one refactor away from breaking.
**Fix:** Two named functions: `build_pin_image_url_for_queue(slug)` → URL with `?v=YYYYMMDD`, `build_pin_image_url_for_ifttt(slug)` → bare URL. All callers use only these. Delete the late-append patch in push_pins_to_sheets.py.

---

## MEDIUM

### M1 — No output contracts between stages (P1)
**File:** generate_posts.py `main()`, all stage functions
**Impact:** `review_and_rewrite()` returns `(content, False, flags)` on failure; caller logs and continues. No exception propagates to GHA. A stage that fails silently produces a held article with no exit signal.
**Fix:** Add `validate_output(stage, result)` to each stage. Raises named exception on failure. GHA step captures non-zero exit.

### M2 — FB Queue dedup does not survive workspace recreation (P6)
**File:** push_pins_to_sheets.py
**Impact:** `sent/` is gitignored. On fresh GHA workspace, `sent_dir.glob('*.json')` is empty. Re-running `push_pins_to_sheets.py` appends duplicate FB Queue rows.
**Fix:** Either un-gitignore `sent/` or maintain a committed `processed_slugs.json` as the dedup record. The `.fired` sentinel pattern (already committed) is the correct model — apply same pattern here.

### M3 — Rewrite prompt make_prompt() uses first-person plural
**File:** generate_posts.py, `make_prompt()`
**Code:** `Write in first person plural ("we tested", "we found", "we noticed").`
**Impact:** Reviewer explicitly fails on first-person voice. Generator is instructed to use it. Reviewer then flags it. This is a contradiction baked into the prompts.
**Fix:** Remove the first-person plural instruction from `make_prompt()`. Replace with second-person or third-person instruction consistent with reviewer rubric.

### M4 — pipeline scripts reach into Brain via raw SQLite
**File:** push_pins_to_sheets.py, `retire_from_products()`
**Impact:** Not a GHA risk (no concurrent access), but a coupling smell. If Brain schema changes, pipeline scripts break silently.
**Fix:** Extract Brain writes to a dedicated `brain_writer.py` helper. Pipeline scripts call the helper, not raw SQLite.

### M5 — P9 stale site reference location
**File:** push_pins_to_sheets.py
**Impact:** Needs `grep` to confirm exact line. Logged as pending verification.

---

## LOW

### L1 — `shutil` import at top of post_pins.py is misplaced
**File:** post_pins.py line 1: `import shutil` before the module docstring
**Impact:** Cosmetic. No runtime effect. Violates PEP 8 (imports after docstring).

### L2 — `check_url_live()` re-imports urllib.request inside function
**File:** post_pins.py
**Impact:** Redundant import. Module already imports urllib at top level.

---

## NEW FINDINGS (not in problem map)

1. **C3** — Fact-check routes OR model to Groq endpoint (breaking every roundup fact-check)
2. **C4** — GHA action versions @v6/@v7 don't exist
3. **M3** — Generator instructs first-person plural; reviewer fails first-person. Direct contradiction.
4. **H4** — Rewrite attempt-2 Groq path always fails on GHA (CF-blocked, was never replaced)

---

## CORRECTED UNDERSTANDING OF P1 (step order)

The queue item #1 ("post_pins before push_pins") describes an older publish.yml architecture. Current publish.yml does NOT call either script. Both are called from pin.yml in the correct order (post_pins → push_pins). The step order bug as described no longer exists. The real bug is H3: no automatic trigger from S2 to S3.

---

## FIX PRIORITY ORDER (revised from handover)

Per P1-P10 from handover + new findings, adjusted for dependencies:

1. P8 — utcnow (10 min, zero risk)
2. C4 — GHA action versions (5 min, zero risk)
3. P9 — stale site reference (grep + one-line)
4. C3 — Fact-check endpoint routing (one function, high impact)
5. P3 — Fact-check truncation (one function)
6. M3 — First-person contradiction in make_prompt()
7. P5 — Pin image URL named functions
8. P6 — FB Queue dedup (processed_slugs.json or un-gitignore sent/)
9. P4 — GENERATION_RESULT.json + GHA exit gate
10. H3 — S2→S3 workflow_run trigger
11. H4 — Rewrite attempt-2 Groq replacement
12. P2 — Reviewer prompt rebuild (largest change)
13. P1 — Output contracts / validate_output()
14. P10 — Chewy validation script (new)
