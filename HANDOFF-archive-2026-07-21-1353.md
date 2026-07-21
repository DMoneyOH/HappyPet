# HappyPet — Session Handoff (2026-07-20)

Ground truth as of commit `8d46e73` on `main`. Working tree clean except two long-standing untracked files (`CLAUDE.md`, `GENERATION_RESULT.json`) — leave them, they are pre-existing and gitignored-in-spirit. Test suite: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q` → **125 passed, 0 failed** (the old 2 Windows-cp1252 failures were fixed this session).

Full review + phased plan (the "F1–F17" item IDs referenced below) lives in `docs/superpowers/specs/2026-07-20-happypet-autonomy-review-and-plan.md`. Auto-memory index: `~/.claude/projects/C--Users-derek-MAEVE-HappyPet/memory/happypet-autonomy-plan.md`.

## 1. Mission

HappyPet is a Jekyll affiliate blog (happypetproductreviews.com) with a Python + GitHub Actions content pipeline (generate → publish → deploy → pin). The goal is to run the **content pipeline autonomously**. This session did a multi-model code review, shipped the autonomy-readiness fixes, and then chased the real blocker: the generator's output can't clear a deliberately strict anti-AI reviewer, so every article gets **held instead of published**. The pipeline plumbing works end-to-end; the open problem is generation *quality vs. the review bar*.

## 2. Current State

**Working and verified (all merged to `main` this session, 13 PRs):**
- Pipeline is hardened for unattended runs: crash-safe atomic `products.json` writes (`json_io.py`), push-retry on all main-writers, orphan-draft prevention, drained-queue signal, Chewy 429 backoff, workflow guards. A CI test-gate (`.github/workflows/test.yml`) runs the suite on every PR — **green on Ubuntu**.
- **Publish blocker F1 fixed + proven on real data:** the gate accepts `amazon.com/dp/...?tag=` links (was `amzn.to`-only); the current 23-entry queue is now publishable.
- **Generator is Claude Sonnet** (`anthropic/claude-sonnet-4.5`) via OpenRouter (Gemini demoted to fallback). Slug verified live (run used it, no fallback).
- **Prompts de-sabotaged:** generation prompt no longer models em dashes / first-person; split into a `system` prompt (`GENERATOR_SYSTEM_PROMPT`, rules in `<writing_rules>`) + XML user task; rewrite prompt re-asserts hard rules and no longer tells the model to "add a human moment" (which caused first-person + fabrication). Temp 0.7→0.5.

**The blocker — two supervised live runs HELD (did not publish):**
- Preflight secrets check: PASS (Gemini, OpenRouter paid tier, Sheets/Gmail/IFTTT/Impact/Facebook all present; PA-API blank as expected).
- Run 1 (Gemini, before swap): `human_voice=2`, em_dashes=12, first-person, fabricated quotes → held.
- Run 2 (Claude Sonnet, before the prompt PRs #63–#65): `human_voice=3`, `warmth=3`, `em_dashes=8→12`; the **rewrites made it worse** (fabricated stats, "My Lab Buster" first-person). That rewrite-degradation is fixed in #64, but **no run has been done since the prompt PRs**.

**Exact next action:** Implement the **deterministic em-dash strip** (F3-adjacent). No LLM — Gemini or Claude — emits exactly 0 em dashes, and the reviewer rule is "any em dash = FAIL." Strip `—` (U+2014 → hyphen/comma) from the article body right before the review gate, mirroring the existing `scrub_banned_phrases`. This is a ~10-line, fully-testable change and is the single fix most likely to flip "held" → "passed." **Then** do one supervised confirmation run (`gh workflow run generate.yml -f force_cap=1`, ~2¢) to see where `human_voice`/`warmth` land — that's the only remaining unknown, and it drives the Open Question about the ≥4 bar.

## 3. Decisions Made (and Why)

- **Decision:** Deliver a plan + fixes, keep the `generate.yml` cron HELD. **Alternatives:** flip live now. **Reason:** Director wants a supervised, verified path; going autonomous is his explicit call. **Reversibility:** load-bearing — do NOT enable the cron without an explicit "go."
- **Decision:** Generator = Claude Sonnet via **OpenRouter**. **Alternatives considered & rejected:** the Max subscription (interactive-only, cannot authenticate a CI pipeline); "free" Claude (OpenRouter's free models are open-weight, not Claude); a direct Anthropic API key (violates the standing "Claude only via OpenRouter, no direct key" rule). **Reason:** reuses the reviewer's proven plumbing, stays in-convention, ~pennies/article. **Reversibility:** model is a one-line constant (`OR_GEN_MODEL_CLAUDE`).
- **Decision:** Em dashes must be handled by deterministic code, not prompting. **Reason:** empirically, both Gemini (12) and Claude (8–12) produce them despite explicit prohibition. **Reversibility:** easy; it's a scrub step.
- **Decision:** Rules moved to a `system` message + XML tags (`system/user` split). **Reason:** Anthropic guidance — Claude obeys system-role, XML-delimited rules far better than a flat user blob. **Reversibility:** contained to `make_prompt`/`call_generator`.
- **Decision:** Self-merge each green, CI-passing PR to drive to done rather than queueing them for review. **Reason:** Director said "I need a working project, not a project car we keep piecing together." **Reversibility:** N/A (process).

## 4. Architecture & Key Files

- **`generate_posts.py`** — the generator. Key symbols this session: `GENERATOR_SYSTEM_PROMPT` (system-role persona + rules), `make_prompt()` (returns XML `<task>/<featured_product>/<structure>` user content), `call_generator(prompt, api_key, system="")` (Tier 1 Claude Sonnet via OpenRouter with system message + temp 0.5, Tier 2 Gemini fallback), `make_rewrite_prompt()` (now re-asserts hard rules), `evaluate_scorecard()` (deterministic pass/fail — thresholds + em-dash-in-body check; **this is where the em-dash strip should feed from / near**), `validate_output()` (publish gate; accepts amzn.to OR amazon.com/dp; `AFFILIATE_LINK_RE`), `validate_product()` (rejects NEEDS_ASIN/NEEDS_IMAGE), `persist_generated_article()` (pin-queue-then-draft, orphan-safe). The `api_key`/`groq_key` params threaded through are **dead** (vestigial) — safe to remove later (F13).
- **`json_io.py`** — NEW. `atomic_write_json()` + `read_json()`/`CorruptJSONError`. Used by all 4 `products.json` writers.
- **`test_pipeline.py`** — 125 tests. New classes this session: `TestProductValidation`, `TestScorecardEvaluation`, `TestProductsJsonAffiliateContract` (loads real `products.json`), `TestJsonIO`, `TestArticlePersistence`, `TestChewyApiRetry`, `TestGeneratorModel`, `TestGenerationPromptHygiene`, `TestGeneratorSystemPrompt`, `TestRewritePromptGuardrails`.
- **`.github/workflows/`** — `test.yml` (NEW, PR gate; installs pytest), plus generate/publish/pin/deploy hardened (push-retry, drained-queue, deploy single-fire). `generate.yml` cron still commented out (HELD).
- **`docs/superpowers/specs/2026-07-20-happypet-autonomy-review-and-plan.md`** — the full F1–F17 review/plan.
- **`chewy_lookup.py`** — `_impact_get()` now retries 429/503.

## 5. Gotchas & Hard-Won Knowledge

- **No LLM emits zero em dashes.** Prompting reduces but never eliminates them. The "any em dash = FAIL" rule therefore *requires* a deterministic strip. This is the crux of why runs keep holding.
- **The prompt was sabotaging itself:** it contained 9 em dashes while forbidding them, and its "good opening" examples were all first-person while forbidding first-person. Fixed (#63/#65), but the lesson: check that a prompt doesn't *model* the behavior it bans.
- **The rewrite loop degraded good drafts.** `make_rewrite_prompt` told the model to "add a concrete human moment" for warmth → it invented first-person anecdotes + fake stats. Fixed (#64). If rewrites regress again, re-check that prompt.
- **`human_voice ≥ 4` may be unwinnable by automated generation.** A `4` means "genuinely non-AI-sounding"; Claude Sonnet reliably produces `3` ("competent but generic"). Even relaxing to ≥3 wouldn't pass a `2`. This is an unresolved product decision (see Open Questions).
- **A hand-written Claude prototype is NOT representative of Claude-Sonnet-via-API.** An earlier free prototype (written by Opus in-session, 0 em dashes, human_voice≈4) was too optimistic; the real API run scored lower and had em dashes. Trust live runs, not in-session prototypes.
- **The Max subscription cannot power the CI pipeline** (interactive auth only). External API key required.
- **CI gate needs `pytest` explicitly** — `requirements.txt` is runtime-only; `test.yml` installs pytest separately.
- **Model slug `anthropic/claude-sonnet-4.5` is live-verified** on this OpenRouter account (a real run used it). Bump the constant for a newer Sonnet if desired.

## 6. Conventions In Play

- **Branch + PR for everything**, never direct to `main`. Branch naming `claude/happypet-recovery-N-<slug>` — **next N is 35**. Data-only changes use `refill/<date>`.
- **TDD** for code: write the failing test, watch it fail, implement, watch it pass. Prompt changes are guarded by content-assertion tests (e.g. "prompt contains ≤1 em dash").
- **Self-merge green PRs** this session (`gh pr merge --merge --delete-branch`) after the suite passes; CI gate also runs on the PR.
- Conventional commits (`feat(...)`, `fix(...)`, `ci(...)`). Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.
- Model constraints: reviewer = Claude Haiku 4.5 via OpenRouter; generator = Claude Sonnet 4.5 via OpenRouter; **no direct Anthropic key**.

## 7. Open Questions

1. **After the em-dash strip + a confirmation run, does `human_voice` clear the bar?** If it lands at 3, the Director must choose: (a) lower `human_voice`/`warmth` pass threshold 4→3 (keep fabrication/first-person as hard holds), (b) try Opus as generator (more cost, uncertain), or (c) human-in-the-loop on held articles. Do not lower the quality bar without an explicit yes — it changes the site's content standard.
2. **When to spend on confirmation runs?** Each is ~2¢ and, if it passes, publishes one LIVE article + Pinterest pin. Director controls timing.
3. **Go-live "go"** for the `generate.yml` cron — unchanged, still his call, after a clean supervised run.
4. Mark **`CI — Tests / pytest`** a required status check in `main` branch protection (UI action, can't be done from code).

## 8. Do Not Touch

- **`generate.yml`'s commented-out `schedule:` block** — the load-bearing hold. No cron without an explicit "go."
- **Reviewer pass thresholds** (`REVIEW_SCORE_MINIMUMS`, human_voice≥4 etc.) — do not relax without the Director's decision (Open Q1).
- **PA-API** — parked; `refill.yml` auto-trigger stays HELD until keys land. Amazon scrape path in `refill_products.py` is deliberately dead.
- **Chewy `COVERAGE_AUTO_ACCEPT`/`SCORE_*` thresholds and `_first_brand_token`** — empirically settled; don't retune casually (F10 tokenization tidy is deferred and needs real-case regression).
- **The direct-Anthropic-key rule** — stay on OpenRouter for Claude.

**Deferred backlog (optional, non-blocking):** F9 auth-failure alerting, F10 Chewy tokenization consistency, F13 remove dead `groq_key`/`api_key` params, F15 IFTTT-key shift-left validation. (F12 already covered by `pin.yml`'s existing failure-alert step.)

## 9. Resume Command

> Read `HANDOFF.md`. Then implement the **deterministic em-dash strip**: strip `—` (U+2014) from the article body immediately before the review gate in `generate_posts.py` (TDD — add a test that content with em dashes comes back with zero, mirroring `scrub_banned_phrases`). Run `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q` (expect all green), then branch `claude/happypet-recovery-35-em-dash-strip`, PR, and merge once CI is green. Do **not** enable the `generate.yml` cron, lower the reviewer thresholds, or trigger a live `generate.yml` run without asking the Director first (a passing run publishes a live article + Pinterest pin). After merging, ask the Director whether to spend ~2¢ on a confirmation run.
