---
name: happypet-stage1
description: Run one HappyPet Stage-1 article end to end -- write (Opus), review (Sonnet), rewrite until it passes the authoritative gate, stage it, and open a PR. Use when producing a HappyPet article via the internal Claude routine.
---

# HappyPet Stage 1 — internal write/review/rewrite routine

Produce ONE publishable article and open a PR. All model work is done by you
(Opus) and a Sonnet reviewer subagent; `stage1_cli.py` does every deterministic
step. Never call OpenRouter or any external model. Run from the repo root with
`./.venv/Scripts/python.exe`.

## Config
- `AUTO_MERGE`:
  - `off` (default) — open the PR and stop; a human reviews and merges.
  - `on` (autonomous) — after the PR's CI is green, merge it and dispatch Stage 2
    so the article goes live unattended. Runs steps 8–11 below.
- `MAX_ATTEMPTS`: 4 (write + up to 3 rewrites).
- `TARGET_BAR`: aim for reviewer scores of 4 on all axes; the enforced floor is
  whatever `stage1_cli gate` returns (currently 3). Record the best scores reached.

## Loop
1. **Pick topic:** `python stage1_cli.py next-topic`. If it prints `{}`, the queue
   is drained — stop and report. Otherwise keep the JSON (`slug`, `system`, `user`,
   `title`, `keyword`, `affiliate_url`).
2. **Write (Opus, you):** produce the article body from `system` + `user`. Write it
   to `LOOP/body.md` (utf-8). It must satisfy the writing rules in `system`.
   Also write a one-line Pinterest description to `LOOP/pindesc.txt`.
3. **Review (Sonnet subagent):**
   - `python stage1_cli.py review-prompt --slug <slug> --body LOOP/body.md > LOOP/revprompt.txt` (the slug carries the title, keyword, and the featured product's verified figures so the reviewer does not flag them as unsourced)
   - Dispatch a subagent with `model: sonnet` whose entire task is: read
     `LOOP/revprompt.txt`, follow it, and return ONLY the scorecard JSON. Save its
     reply to `LOOP/card.json`.
4. **Gate (deterministic):** `python stage1_cli.py gate --body LOOP/body.md --scorecard LOOP/card.json`.
   - `passed: true` → go to step 6.
   - `passed: false` → note `flags`, go to step 5 (unless attempts exhausted).
5. **Rewrite (Opus, you):** if attempts remain,
   `python stage1_cli.py rewrite-prompt --body LOOP/body.md --instructions <flags-file> --title "<title>" --keyword "<keyword>" --affiliate-url "<affiliate_url>" --product-name "<name>" > LOOP/rwprompt.txt`,
   produce the corrected body, overwrite `LOOP/body.md`, and go back to step 3.
   If `MAX_ATTEMPTS` is hit without a pass → **hold**: do not stage; report the best
   scorecard and the blocking flags. (Phase 1: this is a data point, not a failure.)
6. **Stage:** `python stage1_cli.py stage --slug <slug> --body LOOP/body.md --pin-desc "$(cat LOOP/pindesc.txt)"`.
   A non-zero exit means the content contract failed — treat as a hold.
7. **PR:** create a branch, `git add _posts/ _pin_queue/ assets/images/pins/`, commit,
   push, and open a PR titled `stage1: <title>`. In the PR body include: attempts
   used, final reviewer scores, and total tokens/cost if known. Capture the PR
   number. **Do not merge from inside the loop.**
   - `AUTO_MERGE=off` → stop here and report; a human merges.
   - `AUTO_MERGE=on` → continue to Phase 2 below.

## Phase 2 — auto-merge + publish (only when `AUTO_MERGE=on`)
The PR is green-by-construction (the in-loop gate already passed). `CI — Tests`
(`test.yml`) independently re-runs the whole suite on the PR — including the
category-pill and pin-resolution guards — so nothing bad merges even if the loop
misbehaved. Requires **only** the GitHub App's existing PR-merge scope
(`contents` + `pull_requests` write) — publishing **auto-fires on the merge**
(step 10), so **no `actions: write` is needed**.

8. **Wait for CI green:** `gh pr checks <pr> --watch --fail-fast`.
   - Exit 0 → all checks passed; continue.
   - Non-zero → a check failed/was cancelled. **Do NOT merge.** Report the failing
     check and stop (treat as a hold — same as a gate failure).
9. **Merge:** `gh pr merge <pr> --merge --delete-branch`. This lands
   `_posts/DRAFT-<slug>.md` on `main`. Merging a draft cannot publish it early:
   `deploy.yml` only builds DATED posts (`_posts/YYYY-MM-DD-*.md`), so a
   `DRAFT-*.md` is inert until Stage 2 dates it. (If the merge also lands a pin
   image under `assets/`, a harmless no-op rebuild may run — the draft is not in
   it.)
10. **Stage 2 auto-fires on the merge.** Merging the PR lands `_posts/DRAFT-<slug>.md`
    on main, which triggers `publish.yml` automatically (the merge is via the GitHub
    App, not `GITHUB_TOKEN`) — it dates the draft, pushes, and dispatches `deploy.yml`
    → `pin.yml`. No `actions: write` required. As a belt-and-suspenders fallback you
    MAY also run `gh workflow run publish.yml --repo <owner/repo>`; if that returns a
    permissions error, **ignore it** — the merge already triggered publish (a second
    run just finds no draft and exits). Never treat a failed dispatch as a hold.
11. **Verify live:** watch `publish.yml` → `deploy.yml` → `pin.yml` finish; confirm
    the article returns HTTP 200 at its live URL and pins fired. Report the URL.

## Report
Always end with: topic, outcome (staged-PR / held), attempts used, final scores,
and any blocking flags.
