# HappyPet — Session Handoff (2026-07-07)

Read this file, then read the repo state (`git log --oneline -15`, `git status`). This file is ground truth as of commit `162c0dd` on `main` (this handoff's own commit will be one ahead of that, on branch `claude/happypet-recovery-13-handoff`, merged by the time you read this).

## 1. Mission

HappyPet is a Jekyll affiliate blog (happypetproductreviews.com, GitHub Pages) that reviews dog/cat products, monetized via Amazon Associates + Chewy/Impact.com. A GitHub Actions pipeline (refill → generate → publish → deploy → pin) is meant to run mostly unattended twice a week. The project stalled in May; PRs #21–32 were a recovery effort that fixed the pipeline's actual bugs. Right now the pipeline is code-complete and tested but **has never run a full unattended cycle** — Derek wants to personally babysit the first live end-to-end run before flipping the cron back on. This session's job was clearing the two things standing between "code works" and "safe to go live": a dead SMTP password, and a backlog of un-resolved product placeholders.

## 2. Current State

**Working and verified:**
- Secrets preflight (`gh workflow run "Preflight — Secrets Readiness Check" --repo DMoneyOH/HappyPet -f send_test_email=true`) is green: 14 PASS, 2 WARN (both by-design, see below), 0 FAIL. Last run: `28836641892`, 2026-07-07T02:12:45Z, `conclusion: success`. A real test email was confirmed sent via SMTP login as `derekepperson@gmail.com` to `hello@happypetproductreviews.com`.
- Test suite: `cd HappyPet && .venv/Scripts/python.exe -m pytest test_pipeline.py -q` → 45 passed, 1 skipped, 2 failed. **The 2 failures are pre-existing and environment-specific, not regressions**: `TestPendingDraftsJSON::test_publish_yml_reads_pending_drafts` and `TestRefillAgent::test_refill_workflow_never_pushes_main` both fail only on Windows because they call `Path(...).read_text()` on `.yml` files containing an em-dash, and Windows defaults to cp1252 instead of UTF-8. GitHub Actions runners are Linux and won't hit this. Don't "fix" these by touching the workflow YAML — the fix, if ever wanted, is adding `encoding="utf-8"` to the test's `read_text()` calls.
- `GMAIL_ACCOUNT` = `derekepperson@gmail.com`, `GMAIL_SMTP_USER` = `hello@happypetproductreviews.com`, `GMAIL_APP_PASSWORD` = freshly rotated. All three set explicitly via `gh secret set` this session (not just presence-checked — the old `GMAIL_ACCOUNT` value predated the PR #32 code fix and was probably wrong, which is likely why last week's password rotation still failed with SMTP 535 even after the code fix landed).
- The Gmail app password is also backed up in the Brain's encrypted vault (`C:\Users\derek\MAEVE\maeve-brain-v2.db`) as `HAPPYPET__GMAIL_APP_PASSWORD` (all-caps prefix, matching the other `HAPPYPET__*` entries — mind this, see Gotchas).
- Repo cloned locally at `C:\Users\derek\MAEVE\HappyPet` for the first time this session (previously GitHub-only from this machine). Has its own `.venv` with `requirements.txt` installed.
- `gh api repos/DMoneyOH/HappyPet/actions/permissions/workflow` shows `can_approve_pull_request_reviews: true` already — the "Allow GitHub Actions to create and approve pull requests" repo setting Derek was supposed to flip appears to already be on. Not independently confirmed by watching a refill PR auto-open, just confirmed via the API field. If a future refill run still can't open its own PR, this is the first thing to re-check.

**Broken / blocked:**
- **21 of 23 products in `products.json` are still `NEEDS_ASIN`/`NEEDS_IMAGE` placeholders.** This is the actual go-live blocker now. See Decisions Made — Amazon scraping is confirmed unreliable even from a residential IP; the real fix is Amazon PA-API keys, which is a Derek action (Associates Central → Tools → Product Advertising API). The code already supports PA-API as the primary source (PR #31); scraping is only the fallback.
- Go-live itself (uncommenting the `schedule:` cron in `generate.yml`, dispatching with `force_cap=1`, babysitting the full cycle) has not happened and should not happen without Derek explicitly saying "go."

**Exact next action:** ask Derek whether he's gotten PA-API keys yet. If yes, sync `AMAZON_PAAPI_ACCESS_KEY` + `AMAZON_PAAPI_SECRET_KEY` (+ optional `AMAZON_PAAPI_PARTNER_TAG`) as GitHub secrets and re-run `refill_products.py` — it auto-switches to API mode when the keys are present. If no, that's the thing to unblock before touching placeholders again; don't re-run the scrape hoping for better luck (see Gotchas).

## 3. Decisions Made (and Why)

- **Decision:** Store the Gmail app password via the real `SecretVault` API (`src/maeve/secrets/vault.py` in MaeveJarvis), not the plain `sqlite3.connect()` snippet the original handoff document suggested.
  **Alternatives considered:** the handoff's own PowerShell/sqlite3 one-liner.
  **Reason:** `maeve-brain-v2.db` is SQLCipher-encrypted (whole-file) plus each secret value is separately AES-256-GCM encrypted with a key in the OS keyring — plain `sqlite3.connect()` fails with "file is not a database". The handoff document was written before or without accounting for the Piece 0b vault migration.
  **Reversibility:** N/A, this is just "use the correct API."

- **Decision:** Explicitly reset `GMAIL_ACCOUNT` and `GMAIL_SMTP_USER` GitHub secrets to known-correct values, rather than trusting the existing ones were fine.
  **Alternatives considered:** trust the preflight's "present" check and only rotate the password.
  **Reason:** first attempt (password only) still failed SMTP auth with 535. `GMAIL_ACCOUNT` hadn't been touched since 2026-05-18, before the PR #32 code change that started using it as the login identity — good chance it held a stale or wrong value that nobody had verified since the code started depending on it.
  **Reversibility:** Load-bearing but easily redone if wrong — just re-run `gh secret set`.

- **Decision:** Local refill runs and code fixes go on separate branch types — `refill/<date>` for pure data-agent output (mirrors PR #30's precedent), `claude/happypet-recovery-N-<slug>` for code changes. Don't mix a code fix into a data-only branch.
  **Reason:** keeps a code fix reviewable independent of whatever data a given refill run happened to produce; matches existing repo history.
  **Reversibility:** convention, not enforced by tooling — just don't break it without a reason.

- **Decision:** Merged PR #33 (sponsored-ad filter fix) without waiting for CI, based on a local `pytest` run.
  **Alternatives considered:** wait for Derek to review and merge himself.
  **Reason:** this repo has no `pull_request`-triggered CI workflow — nothing to wait for. The 12 prior recovery PRs (#21–32) were already merged following the same "verify locally, then merge" pattern per the original handoff's "Merging authorized after verification" instruction.
  **Reversibility:** already merged; revert if Derek disagrees with the fix's approach (see Architecture section for exactly what changed).

- **Decision:** Discarded the second local refill run's output (0 resolved, +10 new unresolved placeholder topics) instead of committing it.
  **Reason:** it added zero value and just inflates the queue with more entries that will also need to eventually resolve. No point in a PR that's pure noise.
  **Reversibility:** N/A, nothing was committed.

- **Decision:** Recommend Amazon PA-API keys over continuing to iterate on the scraper.
  **Alternatives considered:** keep tuning the scrape (better headers, proxies, delays between requests).
  **Reason:** two runs ~20 minutes apart from the same residential IP went from 2/22 resolved to 0/21 resolved — consistent with rate-limiting/blocking kicking in after repeated traffic, not a fixable parsing issue. The code already has a working PA-API path (PR #31) that's just never been activated because the keys don't exist yet. Continuing to hammer the scrape risks getting Derek's home IP flagged by Amazon for no real gain.
  **Reversibility:** Not a code decision — just a recommendation on where to spend effort next. Doesn't block anything if ignored.

## 4. Architecture & Key Files

- **`refill_products.py`** — Stage 0 agent. Backfills placeholder products + ideates new topics via Gemini, resolves against Amazon (PA-API if keys present, scrape fallback otherwise), opens a review PR. Modified this session: `validate_candidate()` (around line 337) now rejects any candidate whose `name` starts with "Sponsored" (case-insensitive) — see Gotchas for why.
- **`test_pipeline.py`** — 48+ tests (now 50 after this session's addition) covering the whole pipeline; `TestRefillAgent` class (~line 539) covers refill-specific parsing/validation. Added one regression test case for the sponsored-alt-text bug, inside `test_validate_candidate_rejects_bad_data`.
- **`preflight_secrets.py`** — dispatch-only secrets readiness checker. `check_gmail()` (~line 150) logs in as `GMAIL_ACCOUNT` (not `GMAIL_SMTP_USER`) — this was the PR #32 fix; this session's work confirmed the code fix alone wasn't sufficient because the secret *value* also needed correcting.
- **`.github/workflows/generate.yml`** — Stage 1, cron intentionally commented out. **Do not uncomment without Derek's explicit "go".**
- **`.github/workflows/preflight.yml`** — dispatch-only, `send_test_email` input for a real SMTP round-trip check.
- **`docs/handoffs/`** — new this session, mirrors the MaeveJarvis handoff-archival convention (live `HANDOFF.md` at repo root, prior versions snapshotted here before being overwritten). Currently empty (nothing to archive yet — this is the first `HANDOFF.md` ever committed to this repo's history).
- **HappyPet's own `.venv/`** (gitignored) — created this session at `C:\Users\derek\MAEVE\HappyPet\.venv`, has `requirements.txt` installed (pillow, gspread, google-auth, python-dotenv). Use this, not global Python, for any local pipeline script runs.
- **Files that look touchable but shouldn't be:** `push_pins_to_sheets.py` and `brain_secrets.py` still hardcode old local paths (`~/vault/maeve_brain.db`, `../utils/core-skills`) — harmless on CI (they're only used in a local-run code path), and the handoff's own priority list marks this "nice-to-have, fix before running pipeline scripts locally" — but the actual local runs this session did (`refill_products.py`) don't touch those paths, so it wasn't necessary. Don't fix it opportunistically; it's explicitly deprioritized.

## 5. Gotchas & Hard-Won Knowledge

- **GitHub Action secrets are write-only.** `gh secret list` shows names and last-updated timestamps, never values. If you suspect a secret is wrong, you cannot verify by reading it — you can only overwrite it with a known-good value (which is what fixed the Gmail issue) or infer correctness indirectly from a downstream check passing.
- **The Brain vault's secret names are cryptographically bound — no rename.** The `name` column is used as AES-GCM AAD in `SecretVault`. There's no `delete()`/rename method in the public API; re-keying means `vault.use(old_name)` → `vault.put(new_name, value)` → raw `conn.execute("DELETE FROM secrets WHERE name = ?", (old_name,))`. I made exactly this mistake once this session (stored `HappyPet__GMAIL_APP_PASSWORD` mixed-case instead of the `HAPPYPET__` all-caps convention every other HappyPet vault entry uses) and had to re-key it. Check casing before you `vault.put()`.
- **Amazon's mobile search endpoint hides sponsored labeling inside the image `alt` text itself** (literally `alt="Sponsored Ad - <product name>"`), not as a separate `>Sponsored<` tag or `puis-label-popover-default` CSS class the way the desktop endpoint does. `parse_search_results()`'s existing sponsor-skip logic only looks at surrounding markup, so it missed this. Fixed by adding a name-prefix check in `validate_candidate()` instead of trying to catch every markup variant in the parser.
- **Scrape yield is not just noisy, it degrades with repeated same-IP requests in a short window.** First local run: 2/22 resolved. Second run ~20 minutes later, same machine: 0/21 resolved. Don't interpret a bad run as "try again" — it's likely rate-limiting, and repeating it risks the IP getting flagged further.
- **`REFILL_RESULT.json` is gitignored** — it's a run artifact (summary of what a given refill run did), not something that goes in a PR. Only `products.json` changes get committed.
- **Windows `Path.read_text()` defaults to cp1252, not UTF-8.** Any test or script that reads a file containing non-ASCII characters (em-dashes are common in this repo's workflow YAML titles) will throw `UnicodeDecodeError` on Windows unless `encoding="utf-8"` is passed explicitly. This is a Windows-only local-dev issue; GitHub Actions runners (Linux) never hit it. Two pre-existing tests have this bug — don't "fix" it by accident while touching something else, and don't be alarmed when you see it.
- **Temp files holding secrets:** this session used the scratchpad directory (`C:\Users\derek\AppData\Local\Temp\claude\...\scratchpad\`) to stage the Gmail password and Gemini API key as plain files briefly (so they could be piped into `gh secret set` / read by a Python one-liner without living in shell history or command-line args), then deleted them immediately after use. Same pattern is safe to reuse; don't leave these lying around.

## 6. Conventions In Play

- **Branch + PR for everything, never direct commits to `main`.** I broke this once this session (committed a `HANDOFF.md` scaffold straight to `main`) and had to `git reset --hard origin/main` to undo it since it was still unpushed, then redo the work on a proper branch. Don't repeat that mistake.
- **Branch naming:** `claude/happypet-recovery-N-<slug>` for code changes (sequential N, currently up to 13 — this handoff is #13); `refill/<YYYY-MM-DD>[-suffix]` for pure refill-agent data output, no code changes mixed in.
- **Merging:** authorized once verified — this repo has no PR-triggered CI, so "verified" means a local `pytest test_pipeline.py` run, reported in the PR body.
- **Model constraints (do not change without asking):** Generator/rewrites = Gemini 2.5 Flash direct API (fallback OpenRouter `gpt-oss-120b:free`). Reviewer = Claude Haiku 4.5 **via OpenRouter only** — tests enforce absence of `ANTHROPIC_API_KEY` / `api.anthropic.com` anywhere in this repo, including workflow files. Never introduce a direct Anthropic key here.
- **`docs/handoffs/` archival convention**, newly established this session, mirrors MaeveJarvis: live `HANDOFF.md` at repo root; before overwriting it, snapshot the old one to `docs/handoffs/HANDOFF-archive-YYYY-MM-DD-HHMM.md`, commit both together.
- **The Brain vault namespaces secrets as `PROJECT__KEY`, all-caps project prefix** (e.g. `HAPPYPET__`, `FORGE__`, `MAEVETRADER__`, `GOOGLEPHOTOS__`, or `GLOBAL__` for shared creds). Match this exactly.

## 7. Open Questions

1. **Does Derek have Amazon PA-API keys yet, or does he want help navigating Associates Central to get them?** This is the actual blocker on the 21 remaining product placeholders now.
2. **Does he want the missing `HAPPYPET__IMPACT_*` (Chewy) creds backfilled into the Brain vault?** They exist and work as GitHub secrets, just aren't mirrored into the vault (2 credentials were "unrecoverable at salvage" per an earlier memory, rotated since — nobody ever wrote the rotated values back into the vault). Low priority, previously deferred by Derek in this same session.
3. **When is Derek ready to say "go" for the actual live cycle?** Everything else (Gmail, the sponsored-ad bug) is now clear; the placeholder backlog is the last concrete blocker, but go-live is ultimately his call regardless of backlog size — he may want to go live with just the 2 currently-resolved products and let refill catch up over time. Worth asking directly rather than assuming "resolve all 21 first" is required.

## 8. Do Not Touch

- **`generate.yml`'s commented-out `schedule:` block** — do not uncomment without an explicit "go" from Derek. This is the single most load-bearing hold in the whole project.
- **`push_pins_to_sheets.py` / `brain_secrets.py`'s hardcoded old-layout paths** — known, deprioritized, not currently causing any failure. Don't refactor opportunistically.
- **The 2 pre-existing Windows-encoding test failures** — don't "fix" these unless specifically asked; they're not blocking anything (CI is Linux) and touching them is out of scope for whatever else you're doing.
- **PR #33's merged fix** — settled, don't re-litigate the "Sponsored" prefix-matching approach unless a real false-positive shows up (e.g. a legitimately-named product that happens to start with "Sponsored").

## 9. Resume Command

> Read HANDOFF.md. Ask Derek whether he has Amazon PA-API keys yet (Associates Central → Tools → Product Advertising API). If yes, sync `AMAZON_PAAPI_ACCESS_KEY` + `AMAZON_PAAPI_SECRET_KEY` as GitHub secrets on `DMoneyOH/HappyPet` and re-run `refill_products.py` from `C:\Users\derek\MAEVE\HappyPet` (its own `.venv` already has deps installed) to clear the 21 placeholder products. If no, help him get them, and do not re-attempt the Amazon scrape in the meantime. Do not touch `generate.yml`'s cron or run any go-live step without Derek explicitly saying "go." Confirm before merging anything beyond a straightforward, tested bug fix.
