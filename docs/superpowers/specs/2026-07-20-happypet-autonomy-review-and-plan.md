# HappyPet — Autonomy Readiness Review & Action Plan
**Date:** 2026-07-20
**Author:** Claude (Opus 4.8) as lead reviewer, synthesizing a multi-model pass
**Scope of goal (confirmed with Director):** get the **content pipeline** (Stage 1 Generate → Stage 2 Publish → Deploy → Stage 3 Pin) running **unattended**. Product refill/sourcing stays human-in-the-loop (PA-API parked). `generate.yml`'s cron stays **HELD** — this plan does *not* enable it.

---

## 1. Method — multi-model review ("loop with alternate models")

Four independent perspectives, each re-deriving findings cold, then reconciled/adjudicated by the lead:

| Reviewer | Model | Slice |
|---|---|---|
| A | Opus 4.8 (deep) | Generation core — `generate_posts.py`, `generate.yml`, `publish.yml` |
| B | Sonnet | Data/enrichment — `refill_products.py`, `chewy_lookup.py`, `products.json`, validators |
| C | Haiku | CI/CD + ops + secrets — all 8 workflows, `preflight_secrets.py`, `brain_secrets.py`, pin/deploy glue |
| Lead | Opus 4.8 | Reconciliation, firsthand verification of every Critical, adjudication of over/under-calls, prior-audit reconciliation |

The cross-model pass paid off concretely: reviewers A and B **independently** identified the #1 blocker (affiliate-link contract mismatch), which the lead's own pass had missed. Reviewer C surfaced two genuine ops items but also over-escalated two "Criticals" based on an incorrect assumption about shell semantics — corrected in §5.

## 2. Verdict

The pipeline is **substantially more mature and hardened than the standing audit docs suggest** — most 2026-05-19 criticals are already fixed (§3). Architecture is sound: staged workflows, liveness gates before pinning, failure-alert emails, idempotent `.fired` pin sentinels, tracked `sent/` dedup, a real generation exit-gate. **But it is not yet safe to run unattended**, for one blocking reason and a cluster of "fails-quietly" edges:

> **The current `products.json` queue is structurally un-publishable.** The publish gate hard-requires an `amzn.to` link, but 22 of 23 queued products carry full `amazon.com/dp/...?tag=pawpicks04-20` links (which the enrichment pipeline now produces). Every unattended run would burn full LLM budget, hold every article, file issues, and **never go red**. This must be fixed and verified end-to-end before go-live is even conceivable.

## 3. Already resolved since the 2026-05-19 audit (not re-litigated)

Verified fixed in current `main`: fact-check no longer truncates to 1500 chars (`generate_posts.py:917`); Groq fully removed (no misrouted endpoints / dead attempt-2 rewrite); `GENERATION_RESULT.json` exit-gate present; S2→S3 auto-trigger present (via `deploy.yml` `trigger-pins`); `utcnow()` → tz-aware; `os.system()` → `subprocess.run([...])`; `_pin_queue/sent/` tracked for FB dedup; GHA actions pinned to real versions (v4/v5).

## 4. Findings (prioritized, deduplicated, severity-calibrated by lead)

Legend — **QW** = safe quick-win (isolated, high-confidence, testable); **DISC** = needs a decision/discussion; **GATE** = must be done before go-live.

### P0 — Blocks safe autonomy

**F1 · Affiliate-link contract mismatch** — `[Critical]` · flagged by A + B, lead-verified · **GATE**
`validate_output` (`generate_posts.py:531`,`:545`) requires `https?://amzn[.]to/`; the reviewer prompt (`:714`), rewrite-prompt injection (`:747`), and fact-check sanitizer (`:908`) share the assumption. But `refill_products.apply_resolution()` emits `https://www.amazon.com/dp/{asin}?tag={tag}` (root cause), and 22/23 live entries are that shape. Result: every article either (a) is held forever ("no affiliate link"), or (b) the LLM hallucinates a fake `amzn.to/XXXX` to satisfy the regex and publishes a dead, revenue-zero link. The test suite *encodes the bug as correct* (`test_pipeline.py:214-217` asserts the real long-form shape must fail the gate).
**Fix:** align all four spots to accept a tag-bearing `amazon.com/dp/...` link **or** `amzn.to/...` — preferably by checking for the entry's literal `product["affiliate_url"]` in the body plus a generalized fallback pattern. Update the test fixtures to the current long-form shape. **Add a data-contract test that loads real `products.json` and asserts every entry satisfies the gate** (this is what would have caught it). Then run one real long-form entry end-to-end and eyeball the emitted link.

### P1 — High (harden before go-live)

**F2 · `validate_product` has no `NEEDS_ASIN` guard** — `[High]` · B, lead-verified · **QW + GATE**
`generate_posts.py:513` checks `NEEDS_IMAGE` only. A half-resolved placeholder (image fixed, ASIN not) passes the required-field loop (the `dp/NEEDS_ASIN?tag=...` URL is truthy) and publishes a dead link + broken pin image (`:1420`). Zero test coverage on `validate_product`.
**Fix:** also fail when `product.get("asin") == "NEEDS_ASIN"` or `"NEEDS_ASIN" in affiliate_url`. Add `validate_product` unit tests (valid / NEEDS_IMAGE / NEEDS_ASIN / both / missing-field).

**F3 · Reviewer gate trusts the LLM's self-reported `pass`** — `[High]` · A · **QW + GATE**
`passed = scorecard.get("pass", False)` (`generate_posts.py:~1089`) is trusted directly. Numeric thresholds (human_voice≥4, warmth≥4, …), the hard "any em dash = FAIL", and "no first-person" are enforced **only inside the prompt** (`:708-716`), not in code. A miscalibrated Haiku/Gemini scorecard (`pass:true` with low scores, or a miscounted `em_dash_count`) ships bad content unwatched.
**Fix:** recompute `passed` in code from the numeric scores + a deterministic `"—" in content` em-dash check + a conservative first-person regex; AND it with the model's `pass`. (Banned phrases are already scrubbed deterministically — this closes the equivalent gap for the two hardest-line rules.)

**F4 · Concurrent-push race on `main`, no retry** — `[High]` · A (Medium) + C (High), corroborated · **QW + GATE**
Generate / Publish / Pin / chewy_validate each define their **own** concurrency group and all push to `main`. `generate.yml:150-161` does a single `pull --rebase` → `push`. A race (e.g. Stage 2's live cron overlapping) rejects the push non-fast-forward; under GHA's default `bash -eo pipefail` the step fails **red** and the ephemeral runner's just-generated drafts are lost (recoverable next cycle, but a burned cycle nobody notices — and a red Stage 1 also blocks Stage 2's `workflow_run` trigger).
**Fix:** wrap `pull --rebase … push` in a bounded fetch/rebase/push retry loop (3–5 attempts) in `generate.yml` and `pin.yml`. Consider a shared `concurrency` group across all `main`-writers as defense-in-depth (**DISC** — verify it can't deadlock the workflow_run chain).

### P2 — Medium (reliability / observability)

**F5 · Orphan pin-less draft on multi-article partial failure** — `[Medium]` · A · **QW**
Draft written (`:1412`) *before* pin staging + `generated += 1` (`:1440`). An exception in between, on a run where another topic succeeded, commits a pin-less orphan that Stage 2 then publishes (live article, no Pinterest pin). **Fix:** write the draft **last** (temp path → `os.replace` after pin+queue staged), or commit-gate drafts on a matching `_pin_queue/<slug>.json`.

**F6 · Queue-exhaustion reads as red failure & blocks publish trigger** — `[Medium]` · A · **QW**
`generate.yml:56` counts `len(products.json)` (total), not *unpublished*; when drained, `generated=0 & held=0` → `exit 1` (`:121`), conflating "provider outage" with "queue empty" and blocking the S2 `workflow_run`. **Fix:** compute remaining-unpublished (logic already exists at `publish.yml:183-197`); soft-exit when zero remain; reserve hard-fail for genuine outage (queue had work, nothing produced/held).

**F7 · Non-atomic `products.json` writes + no cross-workflow lock** — `[Medium]` · B · **QW**
All four writers (`refill_products.py:578`, `manual_resolve.py:79`, `generate_posts.py:412`, `validate_published_chewy_links.py:218`) use bare `write_text()`; a kill mid-write truncates the file, and the next `json.loads()` (no try/except at load) crashes the whole pipeline. **Fix:** temp-file + `os.replace()` at all four sites; wrap `load_products` parse in try/except with a clear, alerting error.

**F8 · Impact.com catalog search has no 429 backoff** — `[Medium]` · B · **QW**
`find_best_match` calls `search_catalog` up to 4×/product with no delay; the first 429 → `ChewyAPIError` → `chewy_enrich` silently returns the empty sentinel (looks like "not found"), degrading Chewy monetization for the whole batch with no visible error. **Fix:** add retry-with-backoff for 429/503 in `_impact_get()`, mirroring the existing `scrape_chewy_rating()` handling.

**F9 · Credential-expiry silently degrades to free tier; no Stage-1 email alert** — `[Medium]` · A · **DISC**
An expired paid `GEMINI_API_KEY` (401/403) is caught as a generic failover → every article silently routes through free OpenRouter (lower quality, unmonitored). `GMAIL_SMTP_*` are wired into `generate.yml:100-102` but never read by `generate_posts.py`, so Stage 1 has **no** email-alert path. **Fix:** distinguish auth (401/403) from transient errors in `http_post`; file a dedicated GitHub issue when the *primary paid* generator/reviewer fails on auth.

**F10 · Chewy tokenization/brand-extraction inconsistencies** — `[Medium]` · B · **DISC** (careful branch + real-case regression, *not* a casual quick-win)
(a) score gate uses `.split()` but the coverage gate uses `_tokenize()` (punctuation mismatch); (b) `best_match`'s `brand_word = name.split()[0]` (e.g. `"3-pack"`) differs from `_first_brand_token`, skewing candidate ranking before the conflict gate runs. Distinct from the known/out-of-scope `_first_brand_token` quirk. **Fix:** use `_tokenize()` + `_first_brand_token` consistently in scoring — but validate against real cases first (HANDOFF flags this area as empirically settled).

**F11 · FB token health check misdiagnoses non-JSON responses** — `[Medium]` · C · **QW**
A 500/timeout yields empty vars → misreported as "token invalid" (`refresh-facebook-token.yml:16-19`). **Fix:** JSON sanity-check the response before parsing.

**F12 · Stale `.pending-slugs` = up to 24h detection lag** — `[Medium]` · C · **QW**
If `pin.yml` fails after firing but before consuming `.pending-slugs`, detection waits for the next Publish run. **Fix:** have `pin.yml` file an issue immediately if the consume step fails.

### P3 — Low (cleanups, latent bugs, observability)

- **F13** `groq_key` dead-threaded through generator functions; `GROQ_URL`/constants unused — cleanup · **QW** (lead)
- **F14** Windows `cp1252` test failures — one-line `encoding="utf-8"` on `read_text()` in `test_pipeline.py` makes local `pytest` green cross-platform. **HANDOFF "Do Not Touch" says leave it — Director decision required.** · **DISC** (lead)
- **F15** `IFTTT_MAKER_KEY` shift-left validation at top of `pin.yml` · **QW** (C)
- **F16** `gh issue create … || true` swallows API failures in `chewy_validate.yml:90` · **QW** (C)
- **F17** Deploy double-fire: `deploy.yml` triggers on both push-paths *and* `workflow_run` → double deploy + double pin dispatch (idempotent via `.fired`, but wasteful/racy) · **DISC** (lead)
- **F18** `chewy_lookup.py` logs errors to stderr; GHA captures stdout → Chewy errors invisible in `LOGS/` · **QW** (audit addendum, still open)
- **F19** Price parser defaults missing cents to `.99` not `.00` (`refill_products.py:299`); `review_count` requested-but-unmapped; unguarded PA-API `float()` — all in parked/dead paths, fix when revived · **DISC** (B)

## 5. Adjudicated *down* (transparency)

Reviewer C (Haiku) filed two "Criticals" premised on GHA bash steps continuing after a failed command. GHA's default shell is `bash --noprofile --norc -eo pipefail`, so a failed `git pull --rebase` / `mv` / `git add` **aborts the step loudly (red)** — not a silent `exit 0`. The genuine residual risk (no retry on the push race) is captured as **F4**. C's "liveness has no explicit downstream guard" is already covered by GHA's implicit `success()` on custom-`if` steps (pins can't fire after a liveness timeout) — kept only as optional hardening.

## 6. Phased plan of action

**Phase A — "Now batch" (this session, safe quick-wins, each its own branch/PR per convention):**
F1 (the blocker), F2, F3, F5, F6, F7, F8, F11, F12, F13, F15, F16. All are isolated, testable, and either add tests or preserve existing behavior on the happy path. Grouped into a small number of themed PRs (publish-gate correctness; workflow resilience; API hardening; cleanup).

**Phase B — Needs a decision (Director):**
F4 shared-lock strategy, F9 alerting policy, F10 Chewy tokenization (real-case regression), F14 test encoding vs. Do-Not-Touch, F17 deploy double-fire trigger choice.

**Phase C — Go-live readiness gate (before un-holding `generate.yml`'s cron):**
1. F1 + F2 + F3 merged and a real long-form entry generated end-to-end with the emitted link eyeballed.
2. A **supervised** `workflow_dispatch` run of Stage 1 → Publish → Deploy → Pin, start to finish, on a live topic.
3. `preflight_secrets.py` green (all secrets present/live).
4. Alerting confirmed: force a failure in a throwaway run and confirm the GitHub-issue / email path fires.
5. Director gives explicit "go". Only then is the `schedule:` block uncommented (separate, deliberate PR).

## 7. Respected constraints (from HANDOFF "Do Not Touch")

Not enabling any cron; not retuning Chewy `COVERAGE_AUTO_ACCEPT`/`SCORE_*` thresholds or casually patching `_first_brand_token`; not reviving/deleting the dead `resolve_product` scrape path; not touching the 2 Windows-encoding test failures without a Director decision (F14). PA-API remains parked; refill stays HITL.
