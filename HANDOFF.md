# HappyPet — Session Handoff (2026-07-22, post-go-live)

Ground truth: `main` @ `cd5f482`, clean working tree except two long-standing untracked files (`CLAUDE.md`, `GENERATION_RESULT.json`) — leave them, pre-existing. Tests: `./.venv/Scripts/python.exe -m pytest test_pipeline.py test_stage1_cli.py -q` → **187 passed**. Prior handoff archived as `HANDOFF-archive-2026-07-22-1512.md`. Cross-session decision log: `~/.claude/projects/C--Users-derek-MAEVE-HappyPet/memory/happypet-autonomy-plan.md`.

## 1. Mission

HappyPet is an affiliate pet-review blog (happypetproductreviews.com): a Jekyll site with a `generate → publish → deploy → pin` pipeline. Stage 1 (article generation) was replaced with an **internal Claude routine** that writes/reviews/rewrites on the Claude subscription (no OpenRouter, no API key), opens a PR, and hands off to the downstream stages. Goal: hands-off autonomy. **On 2026-07-22 the first article went fully live end-to-end through the pipeline** — the project has now published for real.

## 2. Current State

**LIVE (the headline):** `best-dog-cooling-mat` is published and fully distributed — the first article through the internal-Claude Stage-1 → publish → deploy → pin pipeline, on the Director's explicit "go".
- Article: https://happypetproductreviews.com/dog-gear/best-dog-cooling-mat/ (HTTP 200).
- Pinterest pins **fired** (IFTTT `happypet_pin_dogs` + `happypet_pin_home`); Google Sheets audit + Facebook queue **appended**; `_pin_queue/.pending-slugs` consumed. `.fired` sentinels committed on `main` so it will not re-pin.

**Merged this session (all on `main`):** PR **#76** internal Stage-1 routine (Phase 1); **#77** the cooling-mat article (published live); **#78** pin-image fix; **#79** IFTTT secrets env-fallback; **#80** Sheets/FB-queue secrets env-fallback; **#81** pipeline hardening (publish→deploy dispatch, `pin.yml --autostash`, varied FB message).

**The pipeline now runs `publish → deploy → pin` with no manual nudges** — the three go-live workarounds I did by hand are all fixed in code (see §3). Verified reasoning + YAML lint + unit tests; the two *workflow* fixes get their first real-world exercise on the **next** publish run (can't smoke-test without publishing another article).

**The scheduled Stage-1 cloud routine:** `trig_01MeaAqB7AJsQTCsmBxYNWcM` ("HappyPet Stage 1"), cron `15 8 * * 1,4` (Mon+Thu 08:15 UTC), env `env_01FAP6A4RtRLLr1mRAD9FTL4` (Derek Claude cloud, subscription-backed), model `claude-opus-4-8` + Task tool (Sonnet reviewer), **PR-only mode**. **Its current `enabled` state is UNCONFIRMED.** If enabled, the next Thu run opens a *new* `stage1/<slug>` PR for the following topic. Dashboard: https://claude.ai/code/routines/trig_01MeaAqB7AJsQTCsmBxYNWcM

**Not done — Phase 2 (full autonomy), all Director-gated:** (a) have the routine/SKILL **dispatch `publish.yml` after its PR merges** so a run goes article→live unattended; (b) flip the **`auto_merge`** toggle on (green Stage-1 PRs self-merge instead of PR-only); (c) enable the schedule / **retire `generate.yml`** (deprecated OpenRouter Stage 1, cron HELD).

**Exact next action for a fresh session:** none is blocked — it's a **Director decision** between two paths: (1) wire Phase 2 now, or (2) let the next scheduled Stage-1 run open a PR and shepherd one more article through the now-hardened pipeline under supervision before going autonomous. Also worth doing early: **confirm the routine's `enabled` state** so you know whether a PR will appear Thursday.

## 3. Decisions Made (and Why)

Prior-session decisions that still stand:
- **Internal Claude routine replaces OpenRouter Stage 1** (Opus writes / Sonnet reviews / Opus rewrites). Reason: Director wants "simpler and internal"; removes the reviewer-hallucination and rule-unaware-fact-check failure modes. Reversibility: medium (Stage 2/3 + plumbing untouched).
- **Routine drives deterministic plumbing** (`stage1_cli.py`) for topic-selection/prompt-building/gate/staging; Claude never hand-formats front-matter/pin-JSON. Reversibility: n/a (additive).
- **`authoritative_gate` computes pass from reviewer *scores* + hard-checks, ignoring the reviewer's `pass` boolean**; fabrication check narrowed to explicit verbs; reviewer told which figures are verified. Reason: the old gate held clean articles on hallucinated vetoes. Reversibility: easy, but don't — it's the fix.
- **Enforced bar stays 3; loop targets 4.** Reason: reverting straight to 4 reintroduces the permanent-hold failure PR #69 removed. Data: cloud #77 scored 4/4/4/5, supervised 4/4/4/4. Reversibility: config; Director's call (still open, §7).

New decisions this session:
- **Decision:** Published `best-dog-cooling-mat` live (merged #77, ran `publish.yml`, dispatched `deploy.yml`, ran `pin.yml`). **Reason:** Director's explicit "go for goal on publishing." **Reversibility:** low — a live post + real Pinterest pins; deleting the post is possible, un-pinning is not clean.
- **Decision:** Pin image sources from the curated `product["image"]` (`/images/I/…`), deriving from the ASIN `/images/P/` scheme only as a fallback. **Alternatives:** keep ASIN-primary; ship the vault to CI. **Reason:** `/images/P/{ASIN}` returns a 43-byte placeholder for modern `B0G…` ASINs → text-only pins. **Reversibility:** easy; don't.
- **Decision:** Secret loading in `post_pins.py` + `push_pins_to_sheets.py` resolves **vault first, then env at call time** (mirrors `chewy_lookup.py`); left `brain_secrets.py`'s "None on CI by design" contract untouched. **Alternatives:** make `brain_secrets` itself fall back to env (changes its documented contract + a test); ship `COGNITIVE_DB_KEY` + the encrypted brain to CI (worse security). **Reason:** the old `except ImportError` fallback was dead because `brain_secrets` imports cleanly on a runner. **Reversibility:** easy.
- **Decision:** `publish.yml` **explicitly dispatches `deploy.yml`** after pushing the dated post (added `actions: write`). **Reason:** `GITHUB_TOKEN` pushes do not trigger workflows, so the push-filter auto-deploy never fired. **Reversibility:** easy. (Supersedes the older assumption that Stage 2's commit auto-fires deploy.)
- **Decision:** `pin.yml` consume step uses `git pull --rebase --autostash`. **Reason:** the Sheets step leaves the tree dirty; rebase-before-staging failed (exit 128). **Reversibility:** trivial.
- **Decision:** FB-queue fallback message picks from a pool of 8 hooks **keyed deterministically by slug hash** (`push_pins_to_sheets.py`). **Alternatives:** curate every slug; random choice. **Reason:** every uncurated slug reused one sentence; deterministic keeps re-runs idempotent. **Reversibility:** trivial (edit the pool).

## 4. Architecture & Key Files

- **`stage1_cli.py`** — plumbing CLI the routine drives: `next-topic`, `review-prompt`, `gate`, `rewrite-prompt`, `stage`. No model calls.
- **`.claude/skills/happypet-stage1/SKILL.md`** — the routine's write→review→rewrite→gate→stage→PR procedure (local or scheduled-cloud).
- **`generate_posts.py`** — `authoritative_gate`, `build_verified_facts`, extracted `select_next_topic`/`build_writer_inputs`/`stage_article`. **`stage_article` pin-source fix (PR #78):** prefers curated `/images/I/` image, ASIN `/images/P/` only as fallback. Legacy OpenRouter Stage-1 layer (GENERATOR/REVIEW/REWRITE chains, `call_generator`, `evaluate_scorecard`) still present — retire in Phase 2.
- **`brain_secrets.py`** — read-only client for Maeve's encrypted SecretVault (sibling `MaeveJarvis/`, one level up). By design `get_secret` returns **None on CI** (no vault) and never raises; `get_sheets_creds` raises if the vault lacks the key. **Callers must env-fallback themselves.**
- **`post_pins.py`** (Stage 3 IFTTT) + **`push_pins_to_sheets.py`** (Stage 3 Sheets audit + FB queue) — both now resolve secrets vault-then-env at call time (PRs #79/#80). `push_pins_to_sheets.py` also holds `_FB_HOOKS` (curated per-slug messages) and `_FB_FALLBACK_TEMPLATES` (varied fallback pool, PR #81).
- **`.github/workflows/publish.yml`** (Stage 2, **EDITED PR #81**) — dates `DRAFT-*.md`→`YYYY-MM-DD-*.md`, writes `.pending-slugs`, pushes, **then dispatches `deploy.yml`**. Triggers: cron `0 12 * * 1,4`, `workflow_dispatch`, `workflow_run` from the deprecated `generate.yml`.
- **`.github/workflows/pin.yml`** (Stage 3, **EDITED PR #81**) — `workflow_dispatch` only (dispatched by `deploy.yml`'s `trigger-pins` job when `.pending-slugs` is non-empty). Consume step now `--autostash`.
- **`.github/workflows/deploy.yml`** (UNCHANGED) — Jekyll build + Pages deploy on push to `main` matching `_posts/YYYY-MM-DD-*.md` / `assets/**` / layouts / config; its `trigger-pins` job dispatches `pin.yml` from `.pending-slugs`. NOTE: its inline comment still claims Stage 2's push auto-fires deploy — that's the stale assumption #81 worked around; leave the comment or fix it, but the behavior is handled by publish.yml's explicit dispatch.
- **`test_pipeline.py` / `test_stage1_cli.py`** — **187 tests.** This session added `TestStageArticle` pin-source, `TestPostPinsSecretFallback`, `TestPushPinsToSheetsSecretFallback`, `TestFbMessage`.

## 5. Gotchas & Hard-Won Knowledge

- **`GITHUB_TOKEN` pushes do NOT trigger other workflows** (GitHub anti-recursion). Stage 2's dated-post commit therefore never auto-fired `deploy.yml` — handled now by publish.yml's explicit `gh workflow run deploy.yml`. Any future "committed but nothing deployed" symptom is this.
- **`brain_secrets` imports cleanly on a runner** (heavy deps load lazily inside `_get_vault()`), so `try: from brain_secrets… except ImportError: <env fallback>` leaves the fallback **dead**. `get_secret` returns None on CI and callers must fall back to env **at call time**. This silently broke pin posting from 2026-07-02 until PRs #79/#80.
- **`.fired` sentinels are committed to `main`** (`_pin_queue/.fired/<slug>.fired`). Re-running `pin.yml` **without** `--force` SKIPs already-fired slugs — so a re-run is safe (no double Pinterest post). Use `--force` only to deliberately re-fire.
- **`pin.yml` consume step rebases before staging;** the Sheets step leaves the tree dirty → needs `--autostash` (fixed). Without it: `cannot pull with rebase: You have unstaged changes` (exit 128), `.pending-slugs` not consumed → risk of a duplicate FB-queue append next deploy.
- **Pin image `/images/P/{ASIN}`** returns a 43-byte GIF placeholder for modern `B0G…` ASINs → text-only pin. Use curated `/images/I/…`; `fetch_image` recovers it on the `images-na` host if `m.media-amazon.com` is blocked.
- **Merging any `assets/**` change to `main` triggers `deploy.yml`.** Pins only fire if `.pending-slugs` is non-empty (written by Stage 2). It is absent on `main` now; a stale one would fire pins on any deploy — check before merging asset changes.
- **Cloud-container setup is the fragile part, not our code.** Runs can hang at "setting up cloud container" during Anthropic platform incidents (open bugs #58719/#54685/#55736). Repo files load *after* container setup, so they can't cause it. The PR step needs GitHub write access (the Claude GitHub App).
- **Windows/worktree:** run tests with `./.venv/Scripts/python.exe`; a worktree has no `.venv` (use the main repo interpreter by absolute path). Source has literal U+2014/U+2013; always `encoding="utf-8"`. Console can't print the paw emoji (`\U0001f43e`) under cp1252 — strip it when echoing FB messages locally.

## 6. Conventions In Play

- Branch + PR for everything; `claude/happypet-recovery-N-<slug>` — **next N is 45.** Self-merge green recovery PRs (Director preference). Cloud-routine PRs use `stage1/<slug>` branches, PR-only unless `auto_merge` is on.
- TDD: write failing test, watch it fail, implement, pass. Conventional commits ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.
- **Live publishing is ALWAYS Director-gated.** The first live publish (2026-07-22) happened on an explicit "go"; that authorization does not roll forward to the next article. Project law: this file + `CLAUDE.md` + the Maeve constitution (`C:\Users\derek\MAEVE\CLAUDE.md` → `MAEVE.md`).

## 7. Open Questions

1. **Go autonomous now, or one more supervised run?** The pipeline is hardened; the remaining Phase-2 wiring is (a) dispatch `publish.yml` after a Stage-1 PR merges, (b) flip `auto_merge` on, (c) enable schedule / retire `generate.yml`. Director's call.
2. **Enforced bar 3 vs 4?** Still config-only. Data is thin but good (cloud 4/4/4/5, supervised 4/4/4/4). Raise to 4 only with more clean runs, or the permanent-hold failure returns.
3. **Is the scheduled routine `enabled`?** Unconfirmed. If yes, a new `stage1/<slug>` PR appears Thu 08:15 UTC — is that wanted before Phase-2 decisions are made?
4. **FB fallback topic length:** for roundup titles the `{topic}` is the full de-prefixed title (e.g., "dog cooling mats to beat the summer heat"), which reads long in some templates. Acceptable now; could shorten to the core noun later.

## 8. Do Not Touch

- **`deploy.yml`** — unchanged and load-bearing (Jekyll build + Pages + `trigger-pins`). `publish.yml` and `pin.yml` WERE edited this session (PR #81, authorized) and are hardened — don't casually refactor any of the three; they're the live publish path.
- **`.fired` sentinels and `_pin_queue/` state on `main`** — don't hand-edit; they are the pin dedup/liveness ledger.
- **`evaluate_scorecard` + the OpenRouter model chains** in `generate_posts.py` — legacy; the routine uses `authoritative_gate`. Don't "unify" them (retire in Phase 2).
- **Go-live cron** (`generate.yml` schedule) — HELD. No autonomous publish without the Director's explicit "go."
- **Scheduled routine `trig_01MeaAqB7AJsQTCsmBxYNWcM`** — leave as-is unless intentionally changing mode/schedule; don't recreate it.
- **`CLAUDE.md`, `GENERATION_RESULT.json`** (untracked at repo root) — pre-existing, leave them.

## 9. Resume Command

> Read `HANDOFF.md`. HappyPet's first article is **live** (`best-dog-cooling-mat`, pins fired) and the `publish → deploy → pin` pipeline is hardened to run with no manual steps (`main` @ `cd5f482`, 187 tests — confirm with `./.venv/Scripts/python.exe -m pytest test_pipeline.py test_stage1_cli.py -q`). Nothing is blocked. First, confirm whether the scheduled routine `trig_01MeaAqB7AJsQTCsmBxYNWcM` is `enabled` (a new PR would appear Thu 08:15 UTC). Then ask the Director which path: **wire Phase-2 autonomy** (dispatch `publish.yml` on PR merge + flip `auto_merge` + enable/retire `generate.yml` cron) or **shepherd one more supervised article** through the hardened pipeline first. Do NOT publish another article, enable auto-merge, flip the go-live cron, or edit `deploy.yml`/`publish.yml`/`pin.yml` without the Director's explicit "go."
