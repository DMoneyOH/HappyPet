# HappyPet Pipeline Audit -- Desktop Session Handover
*Written: 2026-05-18 | Author: Maeve (mobile session close)*

---

## Context

The HappyPet pipeline has been patched reactively for multiple sessions. Every Monday and Thursday run produces at least one failure requiring manual intervention. The root causes are structural, not cosmetic. This session is a full audit and rewrite -- not a patch session.

Derek has explicitly requested expert-level work. That means reading the right skills before touching any code, running actual verification commands before claiming anything works, and treating the pipeline as a production system that must run unattended.

---

## Step 1: Load These Skills Before Anything Else

Run in this order. Do not skip any.

```
skill: systematic-debugging      # Root cause first. Always.
skill: verification-before-completion  # No claiming done without proof
skill: vibe-code-auditor         # Structural audit of AI-generated pipeline code
skill: python-pro                # Everything written goes through this standard
skill: bash-pro                  # All GHA shell steps through this standard
skill: maeve-github-devops       # GHA workflow audit and rewrite
skill: engineering-craft         # Code quality standards for the rewrite
skill: jekyll-affiliate          # HappyPet-specific Jekyll/affiliate patterns
skill: avoid-ai-writing          # Full 21-category AI pattern catalog for reviewer prompt
skill: beautiful-prose           # Writing style contract for generation prompt
```

**Read the basin paths directly:**
```
/home/derek/vault/skills-basin/systematic-debugging/
/home/derek/vault/skills-basin/vibe-code-auditor/
/home/derek/vault/skills-basin/python-pro/
/home/derek/vault/skills-basin/bash-pro/
/home/derek/vault/skills-basin/maeve-github-devops/
/home/derek/vault/skills-basin/engineering-craft/
/home/derek/vault/skills-basin/jekyll-affiliate/
/home/derek/vault/skills-basin/avoid-ai-writing/
/home/derek/vault/skills-basin/beautiful-prose/
```

---

## Step 2: Read These Files Before Writing Any Code

```bash
# All five pipeline scripts -- read in full, not grep
cat /home/derek/vault/HappyPet/generate_posts.py
cat /home/derek/vault/HappyPet/post_pins.py
cat /home/derek/vault/HappyPet/push_pins_to_sheets.py
cat /home/derek/vault/HappyPet/chewy_lookup.py
cat /home/derek/vault/HappyPet/generate_pin_images.py

# All three GHA workflows
cat /home/derek/vault/HappyPet/.github/workflows/generate.yml
cat /home/derek/vault/HappyPet/.github/workflows/publish.yml
cat /home/derek/vault/HappyPet/.github/workflows/pin.yml
```

Do not proceed to any fixes until all files above have been read in full. This is the rule that has been violated in every prior session.

---

## Step 3: Run vibe-code-auditor Across All Five Scripts

Apply the vibe-code-auditor skill to each script. Document findings per script:
- Structural flaws
- Silent failure modes (exits 0 on actual failure)
- Missing output contracts (what does this function guarantee to return?)
- State assumptions that don't survive GHA workspace recreation
- Missing validation gates between stages

Capture findings in `/home/derek/vault/HappyPet/AUDIT_FINDINGS.md` before writing a single fix.

---

## Step 4: The Problem Map (Known Issues to Fix)

### P1 -- Pipeline has no output contracts
Every stage assumes the previous stage succeeded. Nothing validates its own output before handing off. A generator that produces 0 articles exits 0. A reviewer that fails silently passes the article. A pin that fires to the wrong image exits 0. 

**Fix:** Add a `validate_output()` function to each stage that runs before handoff and raises a named exception (not a generic Exception) on failure. GHA steps must capture these and exit non-zero.

### P2 -- Reviewer rubric is incomplete
The current reviewer prompt catches ~6 of the 21 AI writing patterns catalogued in `avoid-ai-writing`. It misses: significance inflation, copula avoidance, superficial -ing phrases, synonym cycling, inline-header bullet lists, rule-of-three overuse, and a dozen others. Articles pass review and publish with obvious AI tells.

**Fix:** Rebuild the reviewer prompt from the `avoid-ai-writing` 43-entry replacement table and `beautiful-prose` style contract. Every pattern in the catalog needs a corresponding scored flag in the rubric. This is not a prompt tweak -- it's a full replacement of `make_review_prompt()`.

### P3 -- Fact-check truncates to 21% of article
`fact_check_alternatives()` truncates to 1500 chars. A 7000-char article gets 1500 chars checked. This is theater.

**Fix:** Check full article. If model context limit is a concern, chunk into 3500-char overlapping windows and merge results. Never truncate to less than 60% of article length.

### P4 -- GHA workflow exits 0 on provider failure
`generate.yml` exits 0 even when no articles are generated. `workflow run status: completed/conclusion: success` does not confirm article generation succeeded. This has caused hours of wasted debugging.

**Fix:** `generate_posts.py` must write a `GENERATION_RESULT.json` to repo root with `{articles_generated: N, articles_held: N, articles_failed: N}`. The GHA step reads this after script completion and exits non-zero if `articles_generated == 0` AND `articles_held == 0`. Alert fires on any non-zero exit.

### P5 -- Pin image URL format inconsistency
`build_pin_image_url()` appends `?v=YYYYMMDD` for sheet/queue entries (correct -- busts Pinterest CDN cache). But IFTTT delivery strips query params per platform requirement. The two rules are correct but the pipeline doesn't enforce the distinction programmatically -- it relies on developer memory. This caused the duplicate/wrong-image pin incidents today.

**Fix:** Two separate functions with enforced semantics:
- `build_pin_image_url_for_queue(slug)` -- returns URL with `?v=` suffix (for sheets/JSON)
- `build_pin_image_url_for_ifttt(slug)` -- returns bare URL, no query string (for IFTTT delivery)

`post_pins.py` must call only `build_pin_image_url_for_ifttt()` when constructing the IFTTT payload. Assert this in code with a comment that explains why. Never let raw queue URLs reach IFTTT.

### P6 -- `sent/` is gitignored, breaking GHA dedup
`_pin_queue/sent/` is gitignored. GHA workspace is fresh each run. The `.fired` sentinel (committed 2026-05-18, `7121de6`) fixes the duplicate fire problem but the `sent/` move still fails silently because the files can't be moved to a gitignored directory in GHA. 

**Fix:** Either un-gitignore `sent/` (and commit it), or switch the model: retire processed slugs to a `processed_slugs.json` committed file instead of a directory. The `.fired` sentinel is sufficient for dedup -- `sent/` is just cleanup and can be dropped from GHA entirely.

### P7 -- SHA poll in publish.yml is fragile
The SHA poll that checks if Stage 1 committed a DRAFT before Stage 2 runs has caused multiple failures. It was built to decouple stages but it's unreliable under GHA queue delays.

**Fix:** Replace SHA poll with a committed `PENDING_DRAFTS.json` that Stage 1 writes atomically. Stage 2 reads this file. No polling. No race conditions. File present = drafts exist. File absent = nothing to publish.

### P8 -- `utcnow()` deprecation warnings throughout
`datetime.datetime.utcnow()` is deprecated in Python 3.12. All pipeline scripts use it. Emitting deprecation warnings in GHA logs adds noise and will become errors in future Python.

**Fix:** Global replace `datetime.datetime.utcnow()` with `datetime.datetime.now(datetime.UTC)` across all scripts.

### P9 -- Stale `pawpicks` reference in push_pins_to_sheets.py line 73
A leftover reference to the old site name. Minor but wrong.

**Fix:** Replace with correct site reference.

### P10 -- Chewy link verification is post-hoc
Chewy links are assigned at generation time and verified during the run. But there's no check that already-published articles have valid Chewy links that actually match the reviewed Amazon product. Today's audit found 2 of 5 Chewy-linked articles had wrong-brand links.

**Fix:** Add a `validate_published_chewy_links.py` script that reads all published posts, resolves `affiliate_url` ASINs, compares against `chewy_url` product names using the brand identity gate, and reports mismatches. Run this as a GHA scheduled job weekly, not manually.

---

## Step 5: Fix Priority Order

Execute in this order. Do not jump ahead.

1. P8 (`utcnow` deprecation) -- 10-minute global replace, zero risk
2. P9 (stale `pawpicks` reference) -- one-line fix
3. P3 (fact-check truncation) -- single function change
4. P5 (pin image URL functions) -- two functions, clear semantics
5. P6 (`sent/` gitignore) -- architectural decision, commit the fix
6. P4 (GHA exit 0 on failure) -- `GENERATION_RESULT.json` + GHA step change
7. P7 (SHA poll replacement) -- `PENDING_DRAFTS.json` model
8. P2 (reviewer prompt rebuild) -- largest change, build from `avoid-ai-writing` catalog
9. P1 (output contracts) -- add `validate_output()` to each stage
10. P10 (Chewy validation script) -- new standalone script

---

## Step 6: Verification Protocol (Per Fix)

Before marking any fix done:
```bash
# Python syntax check
python3 -m py_compile <script>.py

# Import check (catches broken references)
python3 -c "import <module>"

# Dry run where applicable
python3 <script>.py --dry-run

# Git diff review before commit
git diff <file>

# After commit: verify GHA run succeeds
# Check logs for WRITE|SKIP|DONE|PASS|FAIL|HOLD signal words
```

Never commit a fix and move on without running the verification chain. This is the rule that has been violated in every session.

---

## Step 7: After All Fixes -- Build the Test Harness

Using `tdd` skill, write a `test_pipeline.py` that:
- Mocks OpenRouter/Gemini API calls
- Generates a test article from a known `products.json` entry
- Validates output contract (word count, affiliate link, no first-person, no em dashes)
- Runs the reviewer against the article
- Confirms reviewer returns valid JSON with all required keys
- Checks fact-check runs on full article (not truncated)
- Verifies Chewy brand gate rejects known mismatch

This test suite runs locally before any GHA dispatch. If it fails locally, nothing gets dispatched.

---

## Open Fix Queue (from prior sessions, still unresolved)

- `workflow_run` S1→S2 trigger (decouple Stage 2 from manual dispatch)
- GH issues #13 and #14 need to be closed

---

## Key Principles for This Session

**From `systematic-debugging`:** Identify root cause before proposing any fix. Read the error. Reproduce it. Gather evidence. The fix comes last, not first.

**From `verification-before-completion`:** Every fix gets a verification chain before it's committed. No exceptions. "It should work" is not evidence.

**From `vibe-code-auditor`:** Treat all five pipeline scripts as AI-generated code that has never been properly audited. Look for: silent failures, missing error boundaries, state assumptions, and output contracts that don't exist.

**From `engineering-craft`:** Write code that a senior engineer would not be embarrassed by. No placeholders. No TODOs. No "this will be fixed later."

**From `python-pro`:** Use Python 3.12+ patterns throughout. Type hints on all public functions. Named exceptions, not bare `Exception`. `datetime.UTC` not `utcnow()`. f-strings not format(). Pathlib not os.path.

The goal of this session is a pipeline that runs Monday and Thursday without Derek touching it.
