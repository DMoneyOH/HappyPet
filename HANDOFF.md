# HappyPet — Session Handoff (2026-07-22)

Ground truth: `main` @ `aaa6536`. Working tree clean except two long-standing untracked files (`CLAUDE.md`, `GENERATION_RESULT.json`) — leave them, pre-existing. Tests: `./.venv/Scripts/python.exe -m pytest test_pipeline.py test_stage1_cli.py -q` → **174 passed**. Prior handoff archived as `HANDOFF-archive-2026-07-22-1343.md`. Cross-session decision log: `~/.claude/projects/C--Users-derek-MAEVE-HappyPet/memory/happypet-autonomy-plan.md`.

## 1. Mission

HappyPet is an affiliate pet-review blog (happypetproductreviews.com): a Jekyll site with a `generate → publish → deploy → pin` pipeline. This session **replaced Stage 1** (article generation) — previously external OpenRouter models in a GitHub Action — with an **internal Claude routine** that writes/reviews/rewrites on the Claude subscription (no OpenRouter, no API key), opens a PR, and hands off to the unchanged downstream stages. Goal: hands-off autonomy with as few false-positive holds as possible.

## 2. Current State

**Done, verified, merged to `main` (`aaa6536`, PR #76):**
- **Phase 1 of the internal routine is built + validated.** New: `authoritative_gate()`, extracted `select_next_topic`/`build_writer_inputs`/`stage_article`, the `stage1_cli.py` plumbing CLI, and the routine skill `.claude/skills/happypet-stage1/SKILL.md`. Suite **174 green**.

**PROVEN END-TO-END IN THE CLOUD (this is the headline):**
- The scheduled cloud routine ran the full loop autonomously and opened **PR #77 — "stage1: Best Dog Cooling Mats to Beat the Summer Heat"** (https://github.com/DMoneyOH/HappyPet/pull/77, branch `stage1/best-dog-cooling-mat`, **OPEN, PR-only, not merged**).
  - **2 attempts:** attempt 1 held itself on `accuracy=2` (unverified alt-product dimensions + templated tradeoff phrasing); it rewrote, then **attempt 2 passed**.
  - **Final scores 4 / 4 / 4 / 5** (human_voice/warmth/readability/accuracy), 0 em dashes, no first-person, affiliate links correct, no invented specs.
  - Files staged: `_posts/DRAFT-best-dog-cooling-mat.md` (~1030 words), `_pin_queue/best-dog-cooling-mat.json`, `assets/images/pins/best-dog-cooling-mat.jpg`, **and a `.gitignore` change** (the cloud agent added one — review it, it's the one non-content file).
  - **This closes the §9.1 feasibility question: the CCR cloud path works** — container setup (post-incident), venv/deps, Opus write, the Sonnet reviewer *subagent in CCR*, gate, rewrite loop, pin-image render, git push, and PR all functioned on the subscription.

**The scheduled routine:** `trig_01MeaAqB7AJsQTCsmBxYNWcM` ("HappyPet Stage 1"), cron `15 8 * * 1,4` (Mon+Thu 08:15 UTC), env `env_01FAP6A4RtRLLr1mRAD9FTL4` (Derek Claude cloud, subscription-backed), model `claude-opus-4-8` + Task tool (Sonnet reviewer), repo `DMoneyOH/HappyPet`, **PR-only mode**, connectors trimmed. It was toggled `enabled=false` after an earlier hang, then the Director restarted it (the run that produced #77). Current enabled state unconfirmed — check it. Dashboard: https://claude.ai/code/routines/trig_01MeaAqB7AJsQTCsmBxYNWcM

**Exact next action for a fresh session:** **review PR #77** — this is the Phase-1 human-review gate (does the cloud-produced article read well? is the `.gitignore` change sensible? is the fallback pin image acceptable — the Amazon product photo fetch errored, non-blocking). Nothing publishes; it's PR-only. Then decide the enforced bar (3 vs 4) and whether to move to Phase 2. **Do not merge #77, enable auto-merge, or go live without the Director's explicit "go."**

## 3. Decisions Made (and Why)

- **Decision:** Replace Stage 1's OpenRouter orchestration with an internal Claude routine (Opus writes / Sonnet reviews / Opus rewrites). **Alternatives:** keep patching the OpenRouter reviewer/gate; a code-free pure-Claude routine. **Reason:** Director wants "simpler and internal"; removes the two failure modes seen (reviewer hallucinated vetoes; rule-unaware roundup fact-check). **Reversibility:** medium — Stage 2/3 + plumbing untouched, so reverting Stage 1 is contained.
- **Decision:** Reuse the deterministic plumbing; the routine (Claude) does only writing/judgment and calls `stage1_cli.py` for topic-selection, prompt-building, the gate, and staging. **Alternatives:** rebuild formatting/validation inside the agent. **Reason:** keeps the 152-test determinism + orphan-safety; Claude never hand-formats front-matter/pin-JSON. **Reversibility:** n/a (additive).
- **Decision:** `authoritative_gate` computes pass from the reviewer's *scores* + hard-checks and **ignores the reviewer's `pass` boolean**. **Reason:** the old `evaluate_scorecard` seeded from `pass` and could only downgrade — the asymmetry that held a clean article on a hallucinated em-dash veto. **Reversibility:** easy, but don't — it's the fix.
- **Decision:** Reviewer learns which figures are verified (`make_review_prompt(verified_facts=…)` via `build_verified_facts` + `review-prompt --slug`); gate's fabrication check narrowed to explicit verbs (fabricat/invent/made up). **Reason:** the supervised run held a publishable article because the reviewer flagged the VERIFIED featured price/rating as "no source" and the gate honored that prose. **Reversibility:** easy; keep it.
- **Decision:** One `auto_merge` toggle: off = PR-only (HITL), on = auto-merge on green. **Reason:** one code path, instant flip either way. **Reversibility:** trivial.
- **Decision:** Stage 1 → Stage 2 handoff is an explicit `gh workflow run publish.yml` after merge (NOT a push trigger). **Reason:** `publish.yml` has no push trigger; it fires on a `workflow_run` from the deprecated Stage-1 workflow. **Reversibility:** easy.
- **Decision:** Enforced bar stays 3; the loop *targets* 4; final bar decided from real output. **Reason:** reverting straight to 4 reintroduces the permanent-hold failure PR #69 removed. Data so far is encouraging (supervised 4/4/4/4; cloud PR #77 4/4/4/5). **Reversibility:** config; Director's call.
- **Decision (now PROVEN, was Director's reservation):** run the routine as a scheduled cloud agent on the subscription. **Reason:** the internal, no-API-key path. **Status:** PR #77 proves it works; the earlier hang was an Anthropic platform incident, not our config.

## 4. Architecture & Key Files

- **`stage1_cli.py`** (NEW) — plumbing CLI the routine drives: `next-topic` (topic + built writer prompt), `review-prompt --slug --body` (reviewer prompt incl. verified-data note), `gate --body --scorecard` (scrub + authoritative pass/fail), `rewrite-prompt`, `stage --slug --body --pin-desc` (writes DRAFT + pin-queue + pin image). No model calls here.
- **`.claude/skills/happypet-stage1/SKILL.md`** (NEW) — the routine's operating procedure (write→review→rewrite→gate→stage→PR loop). Followed by a Claude agent, local or scheduled-cloud.
- **`generate_posts.py`** (MODIFIED) — added `authoritative_gate` (~713) and `build_verified_facts`; extracted `select_next_topic`/`build_writer_inputs`/`stage_article`; `make_review_prompt` gained `verified_facts`; `main()` delegates to the extractions. The OpenRouter Stage-1 layer (GENERATOR/REVIEW/REWRITE_CHAIN, `call_generator`, etc.) still exists — slated for retirement in Phase 2.
- **`test_pipeline.py` / `test_stage1_cli.py`** — 174 tests (new: TestAuthoritativeGate, TestSelectNextTopic, TestBuildWriterInputs, TestStageArticle, TestBuildVerifiedFacts, TestReviewPromptVerifiedFacts, CLI tests).
- **Design docs:** `docs/superpowers/specs/2026-07-21-claude-routine-stage1-design.md`, `docs/superpowers/plans/2026-07-21-claude-routine-stage1-phase1.md`.
- **Downstream (UNCHANGED — do not modify):** `publish.yml` (Stage 2: dates `DRAFT-*.md`→`YYYY-MM-DD-*.md`, writes `_pin_queue/.pending-slugs`, pushes), `deploy.yml` (Jekyll build + Pages deploy + dispatches `pin.yml`), `pin.yml` (Stage 3: liveness-gated IFTTT pins + Google Sheets/FB queue). `generate.yml` (old Stage 1) still present, cron HELD — retire in Phase 2.

## 5. Gotchas & Hard-Won Knowledge

- **Cloud-container setup is the fragile part, not our code.** A run hung at "setting up cloud container" during an Anthropic platform incident (status.claude.com); there are open bugs (#58719/#54685/#55736) on this exact hang. Repo files (`.claude/`, `CLAUDE.md`) do NOT affect container setup (they load after). Repo is PUBLIC → clone needs no auth, but the PR step needs GitHub **write** access (worked for #77). If a future run's container starts but the PR fails, check the Claude GitHub App on the repo.
- **Latent `{{ALTERNATIVE_PRODUCTS}}` bug (fixed):** double-brace inside `make_prompt`'s f-string rendered single-brace, so the old `main()` `.replace("{{…}}")` never fired and roundups shipped a raw placeholder. `build_writer_inputs` matches the single-brace form now.
- **Gate on the SCRUBBED body (fixed):** `cmd_gate` scrubs then gates, so a fixable em dash is auto-corrected, not held. Don't revert to gating on raw.
- **Reviewer noise → gate false-positives:** never honor the reviewer's `pass` boolean, and don't substring-match cautionary prose ("no source") as fabrication. Both caused false holds; fixed via `authoritative_gate` + verified-facts + narrowed keywords.
- **Windows/worktree:** tests run with `./.venv/Scripts/python.exe`; a git worktree has no `.venv` (use the main repo's interpreter by absolute path). Source has literal U+2014/U+2013; always `encoding="utf-8"`. `stage1_cli.py` reconfigures stdout to UTF-8 for piped output on Windows.
- **Cloud PR #77 specifics:** it added a `.gitignore` (review before merge); the pin image is a fallback render because the Amazon product-photo fetch errored in the cloud (non-blocking, but a quality note).

## 6. Conventions In Play

- Branch + PR for everything; `claude/happypet-recovery-N-<slug>` — **next N is 41.** Self-merge green, CI-passing PRs (Director preference). Cloud-routine PRs use `stage1/<slug>` branches and are reviewed, not self-merged (PR-only mode).
- TDD (write failing test, watch it fail, implement, pass). Conventional commits ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.
- Live publishing is ALWAYS Director-gated; go-live cron HELD. Project law: this file + `CLAUDE.md` + the Maeve constitution.

## 7. Open Questions

1. **PR #77 review:** does the article read well enough to define the bar (3 vs 4)? Is the `.gitignore` change sensible? Is the fallback pin image (no Amazon photo) acceptable, or is the image-fetch error worth fixing?
2. **When to go autonomous:** flip `auto_merge` on + add the `gh workflow run publish.yml` handoff + retire `generate.yml` + the go-live "go" — Director's call after a clean PR-only track record.
3. **Cloud reliability:** #77 succeeded, but the container-setup hang recurs (platform incident + open bugs). Keep the supervised fallback (me driving the loop) as a backup path.

## 8. Do Not Touch

- **Downstream workflows** (`publish.yml`, `deploy.yml`, `pin.yml`) — unchanged and working. Stage 1's only job is to produce DRAFT + pin-queue + image and dispatch Stage 2.
- **`evaluate_scorecard`** (legacy OpenRouter gate) and the **model chains** in `generate_posts.py` — the new routine uses `authoritative_gate`, not these. Don't "unify" them.
- **Go-live cron** (`generate.yml` schedule) — HELD. No autonomous publish without the Director's explicit "go."
- **The scheduled routine `trig_01MeaAqB7AJsQTCsmBxYNWcM`** — leave as-is unless intentionally changing mode/schedule; don't recreate it.

## 9. Resume Command

> Read `HANDOFF.md`. The internal Claude Stage-1 routine is built, merged (`main` @ `aaa6536`, 174 tests), and **proven in the cloud** — it opened PR #77 (a `best-dog-cooling-mat` draft, scored 4/4/4/5, PR-only). First action: **review PR #77** (https://github.com/DMoneyOH/HappyPet/pull/77) — read the drafted article, check the `.gitignore` change and the fallback pin image; it is PR-only, nothing publishes. Then help the Director decide the enforced bar (3 vs 4) and whether to move to Phase 2. Confirm `./.venv/Scripts/python.exe -m pytest test_pipeline.py test_stage1_cli.py -q` = 174 passed. Do NOT merge #77, enable auto-merge/go-live, retire `generate.yml`, or touch the downstream workflows without the Director's explicit "go."
