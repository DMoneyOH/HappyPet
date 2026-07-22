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
- `AUTO_MERGE`: `off` for Phase 1 (open the PR and stop). Do NOT merge.
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
   used, final reviewer scores, and total tokens/cost if known. **Do not merge**
   (Phase 1). Stop.

## Report
Always end with: topic, outcome (staged-PR / held), attempts used, final scores,
and any blocking flags.
