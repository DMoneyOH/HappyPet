# HappyPet — Session Handoff (2026-07-21)

Ground truth as of commit `c01213f` on `main`. Working tree clean except two long-standing untracked files (`CLAUDE.md`, `GENERATION_RESULT.json`) — leave them, pre-existing and gitignored-in-spirit. Test suite: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q` → **152 passed, 0 failed**.

Prior handoff archived as `HANDOFF-archive-2026-07-21-1353.md`. Auto-memory index: `~/.claude/projects/C--Users-derek-MAEVE-HappyPet/memory/happypet-autonomy-plan.md` (current through this session).

**DO NOT LOSE PROGRESS:** Five PRs shipped and merged to `main` this session (#66, #67, #69, #71 — plus context). All are done and verified green. Do **not** re-implement, re-debate, or "clean up" any of them. The exact next step is a **Director-gated live smoke-test run**, nothing else. Read §2.

## 1. Mission

HappyPet is a Jekyll affiliate blog (happypetproductreviews.com) with a Python + GitHub Actions content pipeline (generate → publish → deploy → pin). **Primary goal (Director, 2026-07-21): get the pipeline running end-to-end with as few elements causing a stoppage or review-hold as possible.** The plumbing works; the live blocker is that generated articles keep getting **held** by the anti-AI reviewer instead of published. This session removed several hold-causes and then, at the Director's direction, swapped the entire model-routing architecture.

## 2. Current State

**Working and verified (all merged to `main` this session):**
- **#66 — deterministic em/en dash strip.** `scrub_typography()` runs at the top of the `review_and_rewrite` loop, BEFORE `make_review_prompt`/`evaluate_scorecard`. Context-aware: paired em-dash → commas, lone → spaced hyphen, en-dash range → hyphen; pairs never cross sentence boundaries. Real U+2014 can no longer reach the reviewer or the gate.
- **#67 — reviewer↔generator prompt reconciliation.** Shared constants `BANNED_WORDS` / `BANNED_TRANSITIONS` / `BANNED_PHRASES` / `BANNED_INTENSIFIERS` (union of both prompts' prior lists) embedded in the generator system prompt, rewrite prompt, AND reviewer prompt so a term the generator is told to avoid is exactly what the reviewer flags. Generator was also taught the reviewer-only rules (copula, intensifiers, participial openings, synonym cycling, inline-header bullets, superlatives/name-drops).
- **#69 — bar lowered 4→3 + em_dash_count bugfix (Director decision).** `REVIEW_SCORE_MINIMUMS` = `{human_voice:3, warmth:3, readability:3, accuracy:3}`. Reviewer prompt PASS CRITERIA are now DERIVED from that dict (can't drift). Also: `evaluate_scorecard` no longer hard-fails on the reviewer's self-reported `em_dash_count` (reviewer LLMs mislabel hyphens like "extra-large" as em dashes); the deterministic `"—" in content` check governs. em-dash / first-person / fabrication remain hard holds.
- **#71 — 3-stage Writer-Judge-Fixer model routing (Director-directed, THE main deliverable of this session).** Replaced ad-hoc per-function model logic with three ORDERED OpenRouter fallback chains + one generic `call_openrouter_chain()` helper. See §4. Suite 152. `git status` clean on main.

**Two live confirmation runs this session, BOTH HELD (nothing published):**
- Run 1 (after #67, generator was Claude Sonnet 4.5): `human_voice=3`, warmth=3 → held (bar was still 4 then).
- Run 2 (after #69, force_cap=1, run `29851726503`, best-dog-cooling-mat roundup): `human_voice=2`, warmth=3 → held even at the lowered bar of 3. Generator was confirmed Claude Sonnet 4.5 (not a fallback). Nothing published; `main` unchanged.
- **Diagnostic finding from run 2 (still open):** the delivered content violated the reconciled prompt wholesale (Title-Case headings, inline-header bullets, banned words like `leverage`/`robust`/`seamlessly`, "12 em dashes"). ROOT CAUSE: for roundups, `fact_check_alternatives()` (`generate_posts.py:~1100`, called at `~1470` before review) sends the WHOLE article to a rule-UNAWARE model (Gemini primary; `gpt-oss-20b:free` on the 503 fallback) and asks it to "return the COMPLETE article" — it re-emits everything in its own voice, undoing the generator's rule-following. `_sanitize_factcheck_output` only checks length + affiliate links, never style. **This fact-check step is UNCHANGED by #71 and remains the leading E2E blocker for roundups.**

**Exact next action:** Ask the Director whether to run a **live smoke-test** of the new #71 routing: `gh workflow run generate.yml --repo DMoneyOH/HappyPet -f force_cap=1`. This is a LIVE run — if it passes review it publishes one article + a Pinterest pin (~pennies). It needs the Director's explicit "go" every time. After it completes, read the run log for `REVIEW PASS/FAIL | human_voice= warmth=` and `Generated: X | Held: Y` to see how the new (cheaper/weaker) chain scores. Do NOT enable the cron.

## 3. Decisions Made (and Why)

- **Decision:** Convert em dashes context-aware (pairs→commas, lone→spaced hyphen), not delete. **Alternatives:** delete; bare-hyphen replace. **Reason:** bare hyphen (`word-word`) reads as a broken compound; conversion keeps prose readable. **Reversibility:** easy (one function, `scrub_typography`).
- **Decision:** Make the generator's `<writing_rules>` the single canonical rulebook and TEACH the generator everything the reviewer judges (via shared constants), rather than trimming the reviewer. **Alternatives:** trim reviewer checks; sync only the two big lists. **Reason:** Director said "standards stay the same" → can't drop reviewer checks → generator must learn them. **Reversibility:** medium (constants + prompt text).
- **Decision:** Lower `human_voice`/`warmth` bar 4→3. **Alternatives:** try Opus generator; human-in-the-loop; diagnose more. **Reason:** Director's explicit call after runs held at 3 then 2 ("competent but generic" is acceptable). **Reversibility:** LOAD-BEARING content-standard change; do not revert without the Director.
- **Decision:** Stop hard-failing on the reviewer's self-reported `em_dash_count`; trust the deterministic body check only. **Reason:** Haiku mislabeled a hyphen as an em dash and held a clean article; the count is advisory and unreliable. **Reversibility:** easy, but it's a correct bugfix — don't undo.
- **Decision (largest this session):** Replace all model routing with the 3-stage chains below (generator = Claude 3.5 Haiku primary; review = GPT-4o-mini primary; rewrite = DeepSeek V3 primary), all via OpenRouter. **Alternatives considered & rejected implicitly:** keeping Claude Sonnet 4.5 generator / Haiku 4.5 reviewer (the prior #61 architecture). **Reason:** Director's explicit, detailed spec. **Reversibility:** easy (edit the three chain lists at `generate_posts.py:65-79`). **Reservation (documented honestly):** the new models are cheaper/WEAKER than Claude Sonnet 4.5, whose entire rationale in #61 was that weak models scored `human_voice=2`. Whether this chain reaches 3/3 is unproven — only a live run tells us.
- **Decision:** Dropped `provider.require_parameters` from the review call. **Reason:** with it, OpenRouter would refuse to route review fallbacks (Qwen/Mistral) that don't support strict `json_schema`, exhausting the chain. GPT-4o-mini (primary) still enforces the schema natively; fallbacks return best-effort JSON that `_parse_scorecard` reads, falling over on parse failure. **Reversibility:** easy.
- **Decision:** Keep self-merging each green, CI-passing PR to drive to done (Director's standing preference: "I need a working project, not a project car"). **Reversibility:** N/A (process).

## 4. Architecture & Key Files

- **`generate_posts.py`** — the pipeline. Key symbols after #71:
  - `GENERATOR_CHAIN` / `REVIEW_CHAIN` / `REWRITE_CHAIN` (lines ~65-79) — the three ordered OpenRouter fallback chains (primary → fallback 1 → fallback 2). **To change any model, edit these lists — nothing else.**
    - generator: `anthropic/claude-3.5-haiku` → `google/gemini-2.5-flash` → `meta-llama/llama-3.3-70b-instruct`
    - review: `openai/gpt-4o-mini` → `qwen/qwen-2.5-72b-instruct` → `mistralai/mistral-small-24b-instruct-2501`
    - rewrite: `deepseek/deepseek-chat` → `qwen/qwen-2.5-coder-32b-instruct` → `cohere/command-r-08-2024`
  - `call_openrouter_chain(chain, messages, *, label, max_tokens, temperature, response_format=None, parse=None, ...)` (~696) — NEW generic caller. Tries each model in order; falls over on ANY failure (429/5xx/truncation/malformed) and, when `parse` is given, on unparseable output. Raises only when the whole chain is exhausted.
  - `call_generator(prompt, api_key="", system="")` (~727) — Stage 1; system+user messages, temp 0.5, via `GENERATOR_CHAIN`. `api_key` is vestigial (kept for call-site compat).
  - `_call_openrouter_reviewer(prompt)` (~735) — Stage 2; `REVIEW_CHAIN`, `response_format` json_schema + `parse=_parse_scorecard`.
  - `_parse_scorecard(text)` (~729) — strips a ```json fence, `json.loads`; raises to trigger fallover.
  - rewrite step inside `review_and_rewrite` (~1299) — Stage 3; `REWRITE_CHAIN`, `make_rewrite_prompt(title, keyword, content=draft, instructions=critique, ...)`.
  - `evaluate_scorecard(scorecard, content)` (~651) — deterministic gate: numeric mins (`REVIEW_SCORE_MINIMUMS`), `"—" in content` em-dash check, fabrication-keyword flags. **Does NOT trust the reviewer's `pass`, scores, or em_dash_count.**
  - `scrub_typography(text)` (~375) — em/en dash conversion (see #66).
  - `BANNED_WORDS`/`BANNED_TRANSITIONS`/`BANNED_PHRASES`/`BANNED_INTENSIFIERS` (~104-150) — shared prompt vocabulary (see #67).
  - `GENERATOR_SYSTEM_PROMPT` (~924), `make_prompt()` (~950), `make_review_prompt()` (~782), `make_rewrite_prompt()` (~880).
  - `fact_check_alternatives()` (~1100) + `_sanitize_factcheck_output()` (~1068) — **the roundup fact-check step. This is the diagnosed E2E blocker (see §2/§5). UNCHANGED by #71.** Still uses `FACTCHECK_MODEL` (Gemini) → `OR_FACTCHECK_MODEL` (`gpt-oss-20b:free`).
- **`test_pipeline.py`** — 152 tests. New/updated this session: `TestTypographyScrub`, `TestReviewGateStripsEmDashes`, `TestPromptRuleConsistency`, `TestModelRoutingChains` (chain order + primary-first + fall-over + exhaustion + parse-fallover), updated `TestGeneratorModel` (chain head + second-model fallover), `test_all_stages_route_through_openrouter`.
- **`.github/workflows/generate.yml`** — Stage 1 workflow. `workflow_dispatch` with input `force_cap` (1 or 2). `schedule:` cron is **commented out (HELD)**. A pass commits `_posts/…` and pushes → triggers publish/deploy/pin. A held run files a `[Review Required]` GitHub issue and exits green.
- **Auxiliary (out of scope, unchanged):** `find_alternative_products()` (`OR_GEN_MODEL` = `gpt-oss-120b:free`), the fact-check models, `json_io.py`, `chewy_lookup.py`.

## 5. Gotchas & Hard-Won Knowledge

- **The fact-check step sabotages roundups.** It re-emits the entire article through a rule-unaware model right before review, reintroducing em dashes / Title Case / banned words. This is why run 2's content ignored the reconciled prompt despite Claude Sonnet generating it. Fixing this (constrain to alternative sections + run `scrub_typography`/`scrub_banned_phrases` on its output, or replace the full-article LLM rewrite with surgical number-hedging) is the top open lever for the E2E goal.
- **Reviewer LLMs miscount em dashes.** Haiku reported `em_dash_count=1` (then 12) by mislabeling hyphenated compounds. Never hard-fail on the model's count — the deterministic `"—" in content` check is the source of truth. (Already fixed in #69; don't reintroduce the count check.)
- **`scrub_typography` runs before review, so em dashes in the reviewer log are the model's (ignored) self-report, not real U+2014.** A held article is almost never held on em dashes now.
- **Over-constraining the generator can lower `human_voice`.** After #67 piled on more rules, run 2 scored `human_voice=2` (vs 3 before). Watch for this if adding more generator rules.
- **`require_parameters:true` will strand review fallbacks** that don't support strict `json_schema`. Left it off intentionally (#71).
- **`gh run view <id> --log` can't print to the Windows cp1252 console** when the article/log contains `→`/em dashes. Prefix with `PYTHONIOENCODING=utf-8` or grep-filter.
- **Windows/cp1252:** tests read files with `encoding="utf-8"` deliberately; keep it. Source contains literal U+2014/U+2013 — the suite passes on Windows, but any new file-read in tests must set encoding.
- **The Max/Claude-Code subscription cannot power CI** (interactive auth only). All models go through OpenRouter on `OPENROUTER_API_KEY`. No direct provider keys.

## 6. Conventions In Play

- **Branch + PR for everything**, never direct to `main`. Branch naming `claude/happypet-recovery-N-<slug>` — **next N is 39.**
- **Self-merge green, CI-passing PRs** (`gh pr merge <#> --repo DMoneyOH/HappyPet --merge --delete-branch`) after the local suite passes; the `CI — Tests / pytest` gate also runs on the PR (green on Ubuntu).
- **TDD** for code: write the failing test, watch it fail for the right reason, implement, watch it pass. Prompt changes are guarded by content-assertion tests.
- Conventional commits (`feat(...)`, `refactor(...)`, `fix(...)`). Commit messages end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.
- **Live `generate.yml` runs are ALWAYS Director-gated** — a pass publishes a live article + Pinterest pin. Never dispatch without an explicit "go" in the current turn.
- Project instructions in `CLAUDE.md` (untracked, in repo root). Shared knowledge base referenced there.

## 7. Open Questions

1. **How does the new #71 routing actually score?** Unproven live. The models are cheaper/weaker than Claude Sonnet 4.5 — a live smoke-test (Director-gated) is the only way to know if it reaches 3/3. Ask before running (it publishes on pass).
2. **Fix the fact-check step?** It's the leading roundup E2E blocker and is unchanged. Options: constrain it to alternative sections; run deterministic scrubs on its output; replace the full-article LLM rewrite with surgical number-hedging. Needs a direction decision. (Was in the Director's "hold for thoughts" bucket before he pivoted to the routing task.)
3. **Go-live "go" for the `generate.yml` cron** — still the Director's call, after a clean supervised run.
4. Mark **`CI — Tests / pytest`** a required status check in `main` branch protection (UI action, can't be done from code).
5. **Cleanup backlog** — held-article GitHub issues are piling up (#68, plus older #52/#59/#62 for cooling-mats; all-held notice #60). May want to close/triage.

## 8. Do Not Touch

- **The three model chains at `generate_posts.py:65-79`** — these are the Director's explicit spec (#71). Don't "optimize" or swap models unless the Director asks; edit only on request.
- **`REVIEW_SCORE_MINIMUMS` (bar = 3)** — deliberately lowered by Director decision (#69). Do not raise/lower without the Director.
- **`generate.yml`'s commented-out `schedule:` block** — the load-bearing hold. No cron without an explicit "go."
- **The em-dash handling (`scrub_typography` + the single `"—" in content` gate check)** — settled across #66/#69. Do not re-add an `em_dash_count`-based hard-fail.
- **`evaluate_scorecard` deterministic gate** — do not make it trust the reviewer's `pass`/scores/count.
- **PA-API / `refill.yml`** — parked; auto-trigger stays HELD until keys land.
- **The no-direct-provider-key rule** — everything via OpenRouter.

## 9. Resume Command

> Read `HANDOFF.md`. The 3-stage model-routing refactor (PR #71) and the em-dash / prompt-reconciliation / bar-lowering work (#66/#67/#69) are all DONE and merged to `main` (commit `c01213f`) — do not re-implement or re-debate them. Run `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q` to confirm 152 pass. Then ask the Director whether to do a live smoke-test of the new routing (`gh workflow run generate.yml --repo DMoneyOH/HappyPet -f force_cap=1`) — this publishes a live article + Pinterest pin if it passes review, so get an explicit "go" first. Do NOT enable the `generate.yml` cron, change the model chains at `generate_posts.py:65-79`, or raise `REVIEW_SCORE_MINIMUMS`, without the Director's explicit instruction. The leading open issue is the fact-check step re-trashing roundups (§2/§7 Q2) — raise it before assuming a hold is the generator's fault.
