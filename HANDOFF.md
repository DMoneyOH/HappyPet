# HappyPet — Session Handoff (2026-07-06)

Paste this into a new Claude Code session (ideally started **locally** in `C:\Users\derek\MAEVE` so it has machine access — the prior session was a cloud container and could not touch the desktop or the Brain).

---

## 1. What this project is

**HappyPet** (`DMoneyOH/HappyPet` on GitHub) is a Jekyll affiliate blog — **happypetproductreviews.com**, GitHub Pages — reviewing dog/cat products. Monetized via Amazon Associates (tag `pawpicks04-20`, amzn.to links) and Chewy (Impact.com, `chewy.sjv.io`). Content is produced by a GitHub Actions pipeline:

- **Stage 0 `refill.yml`** *(new)* — `refill_products.py` keeps products.json fed: fires after each publish, no-ops unless ≤1 unpublished topic (or forced). Backfills `NEEDS_ASIN`/`NEEDS_IMAGE` entries + ideates up to 10 new topics via Gemini, resolves them against Amazon, opens a **review PR** (never commits to main).
- **Stage 1 `generate.yml`** — `generate_posts.py` (v22.0): picks topics from `products.json`, generates roundup articles, fact-checks, 21-category AI-writing review with up to 2 rewrites, renders pin image, commits `_posts/DRAFT-<slug>.md`. **Cron currently HELD** (commented out) — dispatch-only until the supervised go-live run.
- **Stage 2 `publish.yml`** (Mon/Thu 12:00 UTC cron) — promotes drafts to dated posts.
- **`deploy.yml`** — GitHub Pages build+deploy, then dispatches Stage 3.
- **Stage 3 `pin.yml`** — Pinterest pins via IFTTT webhooks + appends rows to the Facebook Queue Google Sheet (a separate FB poster script of Derek's consumes that sheet).
- **Weekly `chewy_validate.yml`** (Sundays) — validates published Chewy links via Impact API.
- **`preflight.yml`** *(new, dispatch-only)* — verifies all repo secrets with free auth pings; PASS/WARN/FAIL table in job summary. Input `send_test_email=true` sends a real test email to hello@happypetproductreviews.com after SMTP login.
- **`test_pipeline.py`** — 48 tests, all passing.

## 2. Model strategy (Derek's decisions — do not change without asking)

- **Generator/rewrites**: Gemini 2.5 Flash (direct API, `GEMINI_API_KEY`). Fallback: OpenRouter `gpt-oss-120b:free`.
- **Reviewer**: Claude Haiku 4.5 **via OpenRouter only** (`anthropic/claude-haiku-4.5`, `response_format: json_schema strict + provider.require_parameters`). **No direct Anthropic account/tokens — ever.** Tests enforce absence of `ANTHROPIC_API_KEY`/`api.anthropic.com`.
- Fact-check: Gemini Flash Lite. Cost ≈ $0.05/article worst case.

## 3. Everything merged so far (all PRs merged to main)

| PR | What |
|---|---|
| #21–24 | Recovery chain: reviewer truncation fix (root cause of the May stall), DRAFT-parsing bugs, silent-leg repairs (FB Queue marker collision, Chewy validation never ran, duplicate pins), SEO/leak fixes, hybrid model strategy |
| #25 | Hotfix: `_config.yml` exclude list clobbered Jekyll defaults → CI vendor/ build failure. Fixed; deploy green (run #324), site redeployed |
| #26–27 | Secrets preflight workflow + pruned 9 already-published topics from products.json; removed dead GROQ/GOOGLE_CSE secrets |
| #28–29 | Stage 0 refill agent + hardening (gzip, mobile endpoint fallback, ideation tokens 16384) |
| #30 | First refill output: 2 products resolved live + 10 new researched topics |
| #31 | Amazon **PA-API v5** support (stdlib SigV4) as primary source when keys present; `.svg` image guard; Texsens image reset to NEEDS_IMAGE |
| #32 | Preflight Gmail fix (login as `GMAIL_ACCOUNT`, not the hello@ alias) + `send_test_email` input |

## 4. Current state (verified on main)

- **products.json**: 23 entries. Ready to generate: `best-dog-cooling-mat` (first in queue), `best-dog-training-treats` (Blue Buffalo + live Chewy link). 21 placeholders held safely by `validate_product()`.
- **Stage 1 cron**: commented out in generate.yml ("HELD for go-live 2026-07-03"). Only `workflow_dispatch` (input `force_cap`).
- **Secrets preflight (latest run)**: 13 PASS · 2 WARN (IFTTT untested-by-design; PA-API keys absent) · **1 FAIL: `GMAIL_APP_PASSWORD` — SMTP 535, password is dead** (verified with correct login identity). Non-blocking: only queue-low alert emails.
- **OpenRouter**: paid tier, credits present. **Impact/Chewy**: working. **Sheets SA + FB Queue sheet**: working (992 rows). **Facebook page token**: valid.
- **Amazon data workaround**: no PA-API keys yet → scrape with hold-on-failure. Success varies by runner IP (2/22 one run, 0/21 another). Every refill run retries placeholders automatically. A **local run** (`python3 refill_products.py` with `FORCE_REFILL=1`, `GEMINI_API_KEY`, `IMPACT_*` env) from a residential IP should resolve nearly all.

## 5. Outstanding work (in priority order)

1. **Gmail app password** (Derek action): generate new app password for the `GMAIL_ACCOUNT` mailbox (Google → Security → 2-Step Verification → App passwords, strip spaces). Store in Brain **and** push to GitHub secret `GMAIL_APP_PASSWORD`:
   ```powershell
   $db = "C:\Users\derek\MAEVE\maeve-brain-v2.db"   # live Brain DB (backups\ = snapshots)
   $pw = Read-Host "App password (no spaces)"
   $env:HP_DB = $db; $env:HP_PW = $pw
   python -c "import sqlite3, os; db = sqlite3.connect(os.environ['HP_DB']); db.execute('DELETE FROM vault_secrets WHERE project=? AND key=?', ('HappyPet','GMAIL_APP_PASSWORD')); db.execute('INSERT INTO vault_secrets (project, key, value) VALUES (?,?,?)', ('HappyPet','GMAIL_APP_PASSWORD', os.environ['HP_PW'])); db.commit(); print('Brain: stored')"
   $pw | gh secret set GMAIL_APP_PASSWORD --repo DMoneyOH/HappyPet
   Remove-Item Env:HP_PW, Env:HP_DB; $pw = $null
   ```
   ⚠️ `vault_secrets` schema unverified in the v2 DB — if the insert errors, inspect `SELECT sql FROM sqlite_master WHERE name='vault_secrets'` and adapt. GitHub secret is what CI reads; Brain is record-keeping.
   Then verify: dispatch **Preflight — Secrets Readiness Check** with `send_test_email=true` → test email arrives at hello@.
2. **Repo setting** (Derek, 30 seconds): Settings → Actions → General → Workflow permissions → check **"Allow GitHub Actions to create and approve pull requests"** — refill runs currently can't open their own PRs (run #2's PR had to be opened manually).
3. **Resolve remaining 21 product placeholders**: either run refill locally (best), keep letting CI retry, or get PA-API keys (Associates Central → Tools → Product Advertising API) and sync as `AMAZON_PAAPI_ACCESS_KEY` + `AMAZON_PAAPI_SECRET_KEY` (+ optional `AMAZON_PAAPI_PARTNER_TAG`) — the agent auto-switches to API mode.
4. **GO-LIVE (needs Derek's explicit "go")**: uncomment the `schedule:` block in `.github/workflows/generate.yml` → dispatch generate.yml with `force_cap=1` → babysit end-to-end: S1 (watch for `START v22.0 … reviewer=anthropic/claude-haiku-4.5`) → publish → deploy → **exactly one pin** → **one FB Queue row** (Derek eyeballs columns: Title, URL, Message, Image, OrigDate, SchedDate, Species, Posted, PostID). This run also proves: Gemini billing tier (free tier ⇒ falls to OpenRouter emergency path — flag it), and IFTTT delivery.
5. **Local-path migration** (nice-to-have): pipeline local-mode code still hardcodes old layout — `push_pins_to_sheets.py` → `~/vault/maeve_brain.db`; `brain_secrets.py` → `../utils/core-skills`. Everything now lives under `C:\Users\derek\MAEVE`. Harmless on CI; fix before running pipeline scripts locally.
6. **Phase 4 candidates** (not started, discuss first): self-hosted product images, Pinterest API direct (replace IFTTT), real FB token refresh, conversion analytics, Product/ItemList JSON-LD.

## 6. Hard constraints & house rules

- **No content generation / cron re-enable until Derek says "go".** The hold is deliberate.
- **No direct Anthropic API/tokens** — Claude only via OpenRouter.
- All changes to HappyPet: **branch + PR** (`claude/happypet-recovery-N-<slug>` naming). Merging authorized after verification. The refill agent's own output must stay PR-reviewed.
- Design principles from the audit: one git-marker one writer; every stage emits a result file and fails red instead of green-while-broken; hold-on-failure for anything unverified (placeholders can't publish).
- `products.json` field notes: `REVIEW:`-prefixed `chewy_url` = flagged brand mismatch, generator safely ignores it. `amazon_search_query` on newer entries = retry query for the refill agent. Amazon images must be `m.media-amazon.com` and not `.svg`.
- HappyPetBeta repo is empty/abandoned — all work happens in `DMoneyOH/HappyPet`.

## 7. Verification protocol (once go-live happens)

Success = two consecutive untouched Mon/Thu cycles: article → publish → deploy → exactly one pin → one FB row, all gates honest. First Sunday chewy_validate now produces a real report (may file an issue — expected, review it).
