# Design — Stage 1 as an internal Claude routine (write→review→rewrite→PR)

**Date:** 2026-07-21
**Status:** Design approved (brainstorm). Ready for implementation-plan.
**Owner:** Director (Derek) + Maeve
**Supersedes for Stage 1:** the OpenRouter 3-stage routing (`generate_posts.py` `GENERATOR_CHAIN`/`REVIEW_CHAIN`/`REWRITE_CHAIN`, PRs #71/#74) and the `generate.yml` GitHub-Actions workflow.

## 1. Context & Motivation

HappyPet's content pipeline is `generate → publish → deploy → pin`. Stage 1 (generate) currently runs `generate_posts.py` in a GitHub Action (`generate.yml`), calling external models via OpenRouter in a fixed 3-stage chain (writer → judge → fixer). Two live smoke-tests this session showed the ceiling of that approach:

- The cheaper chain (Claude 3.5 Haiku writer) held at `human_voice=2` (bar 3).
- Promoting the writer to Claude Sonnet 5 (PR #74) **fixed voice** — all four scores cleared the bar (human_voice 2→3) — but the article **still held**, because the OpenRouter reviewer (GPT-4o-mini) **hallucinated an em-dash** and returned `pass=false`, which the deterministic gate honored (it can only downgrade, never rescue). See run `29860149552`, issue #75.

Two structural problems surfaced: (a) rigid, fixed-model machinery that can't reason about a bogus hold, and (b) the roundup `fact_check_alternatives()` step re-emitting the article through a rule-unaware model. Both are artifacts of orchestrating **external** models through brittle glue.

**Director's decision:** go **simpler and internal** — replace Stage 1's external-model orchestration with a **Claude routine** that writes with Opus, reviews with Sonnet, and rewrites with Opus in an agentic loop until an article meets standards, then pushes to the repo to trigger the unchanged publish/pin stages. Primary driver: **hands-off autonomy**, gated behind a human-review period and a config toggle.

## 2. Goals / Non-Goals

**Goals**
- Replace Stage 1's external-model generation with an internal Claude write→review→rewrite loop (Opus writer, Sonnet reviewer, deterministic backstop).
- Produce articles that clear the review bar, staged through the **existing deterministic plumbing** and published through the **existing** Stage 2/3 cascade, unchanged.
- Support both **HITL** (routine opens a PR, human approves) and **autonomous** (auto-merge on green) via a single `auto_merge` toggle.
- Retire the OpenRouter dependency and the roundup fact-check step for Stage 1.

**Non-Goals**
- No change to Stage 2 (publish), deploy, or Stage 3 (pin).
- No change to the `products.json` schema, dedup, or the queue.
- No change to PA-API / `refill.yml` (parked, HELD).
- Do **not** raise the enforced content bar to 4 without proving it is reachable (Phase 1).

## 3. Key reframe: "deprecate Stage 1" ≠ "delete `generate_posts.py`"

Most of `generate_posts.py` is not model calls — it is **deterministic plumbing** the routine still needs and that carries the 152-test suite:

- Gate: `evaluate_scorecard`, `scrub_typography`, `scrub_banned_phrases`, `validate_output`.
- Staging: `front_matter`, `persist_generated_article` (pin-queue-then-draft orphan-safety), `build_pin_image_url_for_queue`, affiliate-link handling.
- Queue: `load_products`, slug dedup, `products.json` read.

**We deprecate the GitHub-Actions Stage-1 *workflow* (`generate.yml`) and the OpenRouter LLM-calling layer only.** The routine **reuses the plumbing** and replaces just the *brains* — how article text is written, reviewed, and rewritten.

## 4. Architecture

```
Claude routine (Opus main agent):
  [plumbing CLI] read products.json → pick next unpublished topic + product data
  ┌─ loop (max N attempts) ──────────────────────────────────────────┐
  │  Opus writes/ rewrites draft body (existing writing_rules prompt) │
  │  Sonnet reviewer subagent scores vs rubric → scorecard JSON       │
  │  [plumbing CLI] deterministic gate: scrub_typography +            │
  │                 evaluate_scorecard(scorecard, body)               │
  │  standards met?  ── no ─→ Opus rewrites from critique ────────────┤
  └──────────────────── yes ─────────────────────────────────────────┘
  [plumbing CLI] stage article: front_matter + validate_output +
                 persist_generated_article (pin-queue + DRAFT-*.md)
  commit on a branch → open PR
  toggle auto_merge?  ── off ─→ PR waits for human approval (HITL)
                      └─ on ──→ auto-merge when required checks green
  merge to main → routine DISPATCHES Stage 2 (see §9.2 — publish.yml has no
                  push trigger) → deploy → Stage 3 (pin), all unchanged
```

### 4.1 Components

- **Plumbing CLI (new, thin):** a small command surface over the reused `generate_posts.py` deterministic functions, callable by the agent via Bash. Minimum verbs:
  - `next-topic` → emit the next unpublished topic + its product data (JSON).
  - `gate --body <file> --scorecard <file>` → run `scrub_typography`, then an **authoritative** pass evaluation: `passed = (all reviewer scores ≥ REVIEW_SCORE_MINIMUMS) ∧ (no real em dash in the scrubbed body) ∧ (no fabrication flags)`. The reviewer's `pass` boolean is **ignored** (advisory only) — this is deliberately unlike today's `evaluate_scorecard`, which seeds from `pass` and only downgrades, the asymmetry that held today's clean article. Emit `{passed, flags, scrubbed_body}`.
  - `stage --body <file> --topic <slug>` → `validate_output` + `front_matter` + `persist_generated_article`; write `_posts/DRAFT-*.md` + pin-queue entry.
  This isolates all determinism/tests in Python; the agent never re-implements formatting or validation.
- **The Claude routine (new):** an Opus agent (a skill/prompt + subagent orchestration) that runs the loop, calls the plumbing CLI, and drives git (branch/commit/PR). Runs **supervised/interactive in Phase 1**, as a **scheduled cloud agent in Phase 2**.
- **Sonnet reviewer (subagent):** receives **only** the article body + the existing rubric (`make_review_prompt`), returns the scorecard JSON. Given only the body (not the writer's reasoning) to preserve judge independence.
- **CI content-gate check (new, required for auto-merge):** on the PR, re-run the deterministic hard-checks on the committed article file (`"—" in body`, banned phrases, affiliate-link presence, `validate_output` structure) + `pytest`. This is the un-overridable backstop that also holds in autonomous mode.

### 4.2 Deprecated

- `generate.yml` (Stage-1 workflow): retired in Phase 2.
- OpenRouter Stage-1 layer in `generate_posts.py`: `call_openrouter_chain`, `call_generator`, `_call_openrouter_reviewer`, the rewrite call, `GENERATOR_CHAIN`/`REVIEW_CHAIN`/`REWRITE_CHAIN`, and the roundup `fact_check_alternatives()` / `_sanitize_factcheck_output()`. (Removed once the routine replaces them; keep the prompts/rubric constants the agent reuses.)

## 5. "Standards met", the toggle, and "green"

- **"Standards met" (in-loop, to open a PR)** = the deterministic hard-checks pass **AND** the Sonnet reviewer's scores clear the bar. The gate decides pass from the reviewer's **scores** + the hard-checks and **ignores the reviewer's `pass` boolean** (the field that wrongly held today's clean article). Sonnet **advises**; the deterministic gate is **authoritative**. The em-dash / fabrication / affiliate hard-checks are un-overridable — the agent may rewrite to satisfy them but cannot merge around them.
- **`auto_merge` toggle:** one config flag.
  - `off` (default, HITL) → routine opens the PR and stops; human approves/merges.
  - `on` (autonomous) → routine enables GitHub auto-merge; PR merges when required checks are green.
- **"Green" (for auto-merge)** = the CI content-gate check + `pytest` pass. Because the routine only opens a PR after the in-loop gate passes, the PR is green-by-construction; CI re-verifies the deterministic hard-checks independently so nothing bad merges even if the routine misbehaves.

## 6. Bar policy & can't-converge policy

- **Bar aspiration = 4** ("revert to original"), but **not committed until proven** (Phase 1). Today's data: an Opus writer + Sonnet reviewer is the strongest internal config; whether it reaches 4 on real topics is unproven.
- **Phase 1 enforced bar = 3** (current), while the loop *targets* 4 and surfaces the real articles for human review — so we calibrate whether 4 is the right/attainable target from actual output, not a guess.
- **Can't-converge (after N attempts):**
  - HITL mode → leave a draft PR / file an issue for review (never publish sub-standard).
  - Autonomous mode → hold + notify (GitHub issue), do not publish. Matches today's "held articles never publish silently."
- N (max attempts) and the final committed bar are decided from Phase-1 evidence.

## 7. Model roles & independence

- **Writer/rewriter = Opus** (ceiling model; no higher to escalate to).
- **Reviewer = Sonnet** (separate, strong independent judge — less prone to the Haiku em-dash false-positive that blocked us today).
- Cross-tier independence: reviewer is a distinct model instance seeing only the article + rubric.

## 8. Phasing

- **Phase 1 — Build + prove (supervised, `auto_merge=off`):** build the plumbing CLI + the routine loop; run on a few real topics (including the 4×-held `best-dog-cooling-mat` roundup); Director reviews the PRs; measure **tokens-per-published-article** under subscription usage. **Decision gate:** is quality good, is 4 reachable (or is 3 the right bar), does cost fit allowances?
- **Phase 2 — Autonomise (only if Phase 1 clears, flip `auto_merge=on`):** wrap the proven loop as a scheduled cloud routine, add the CI content-gate as a required check, enable auto-merge on green, set cron (Mon+Thu, matching today). Retire `generate.yml`. Verify cloud-routine feasibility first (§9).

## 9. Open questions / verify items

1. **Scheduled cloud routine feasibility (Phase 2 blocker):** does a scheduled Claude routine (a) run under the Max subscription allowance vs. separate credits, (b) have headroom for ~1–2 articles × several rewrites × 2/week of Opus+Sonnet, (c) perform authenticated git push + open-PR from its environment, (d) run reliably on cron? Verify via the Claude Code guide + a supervised dry run before flipping the cron.
2. **Stage 1 → Stage 2 handoff — DECIDED: explicit dispatch.** `publish.yml` has **no `push` trigger**. It fires on `workflow_run` from the workflow named *"Stage 1 — Generate Articles"*, plus its own Mon+Thu 12:00 UTC cron (safety net) and manual dispatch. So deprecating `generate.yml` **breaks the automatic handoff** — a plain merge of `_posts/DRAFT-*.md` will NOT start publishing. **Chosen mechanism:** the routine runs `gh workflow run publish.yml` immediately after the merge. Rejected alternatives: adding a `push` trigger to `publish.yml` (any `_posts/` push would trip Stage 2 — too implicit); keeping a stub "Stage 1" workflow to preserve `workflow_run` (dead scaffolding). The Stage-2 Mon+Thu cron stays as a safety net. Downstream (deploy, Stage 3) is unaffected — it keys off Stage 2's dated-post push + `.pending-slugs`. Only remaining unknown: that the routine's environment can run an authenticated `gh workflow run` — folded into §9.1.
3. **Cost fit:** measured in Phase 1 (tokens-per-article), projected against the autonomous cadence.

## 10. Decisions made (and why)

- **Internal Claude loop over external OpenRouter chain.** *Why:* removes the two failure modes seen this session (reviewer hallucinated veto; rule-unaware roundup fact-check) and the brittle glue; Director wants "simpler and internal." *Reversibility:* medium — Stage 2/3 and the plumbing are untouched, so reverting Stage 1 to OpenRouter is contained.
- **Reuse the deterministic plumbing; replace only the brains.** *Why:* keeps the 152-test determinism and orphan-safety; shrinks what we build. *Reversibility:* n/a (additive).
- **Deterministic gate stays authoritative; reviewer advises.** *Why:* the un-overridable backstop is what makes unattended self-publish safe (independent judge + hard checks). Consistent with the existing `evaluate_scorecard` philosophy. *Reversibility:* easy.
- **Single `auto_merge` toggle for HITL ↔ autonomous.** *Why:* one code path, instant flip either direction, no rebuild to change autonomy posture. *Reversibility:* trivial (config).
- **Opus writer / Sonnet reviewer.** *Why:* strongest internal writer + a strong independent judge (avoids the Haiku false-positive class). *Reversibility:* easy (role config).
- **Bar 4 aspired, 3 enforced until proven.** *Why:* reverting straight to 4 reintroduces the permanent-hold failure that PR #69 removed; prove reachability on real output first. *Reversibility:* config; do not commit to 4 without Director sign-off on Phase-1 evidence.
- **Retire the roundup fact-check step.** *Why:* Opus hedges unverifiable stats as it writes, eliminating the rule-unaware re-emit that reintroduced today's em dash. *Reversibility:* medium.
- **Stage 1 → Stage 2 handoff = explicit `gh workflow run publish.yml`** (not a `push` trigger). *Why:* `publish.yml` starts on a `workflow_run` from the now-deprecated Stage-1 workflow, so the handoff must be re-established; an explicit dispatch keeps Stage 2's start a single deliberate action rather than something any `_posts/` push could trip. *Reversibility:* easy (it's one call; the Stage-2 cron is a fallback).

## 11. Testing

- Deterministic plumbing keeps its 152 tests; the plumbing CLI gets thin tests over its verbs (`next-topic`, `gate`, `stage`).
- The CI content-gate check gets tests (rejects em-dash/banned/missing-affiliate/invalid-structure articles).
- The agentic loop's orchestration (attempt counting, escalation-to-hold, toggle behavior) gets tests with the model calls stubbed.
- Model *quality* is inherently non-deterministic — validated by the Phase-1 human-review period, not unit tests.

## 12. Out of scope

Stage 2/3 workflows, `products.json` schema, PA-API/`refill.yml`, the go-live cron for anything other than this routine, and the separate review-gate false-positive fix (PR-parked; the internal Sonnet reviewer + authoritative deterministic gate makes it moot for this path).
