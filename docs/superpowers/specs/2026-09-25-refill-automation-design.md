# Refill automation: design (spec only, nothing built)

Date: 2026-09-25. Status: DRAFT, decisions resolved 2026-09-25 (build not started). Author: Max. Provenance: `[INTERNAL]` unless a line carries a docs citation (`[EXTERNAL: code.claude.com]`).

## Goal

Refill runs with no human in the loop: a local scheduled headless Claude Code session resolves placeholders through SiteStripe in Chrome, a narrow gate merges the PR, and `chewy_enrich.yml` adds Chewy links. Chrome is an accepted dependency. The Director's stated goal (2026-09-25): refill is 100% automated after go-live, "I don't want to be involved at the ending point". He approves each build step once; after go-live the run goes start to finish with no involvement from him. That goal approves the direction only. Each Class-3 activation in the build order below still needs his explicit approval and the two-clone review.

## Flow

1. **Trigger.** Windows Task Scheduler runs `run-refill.ps1` every other Sunday, in the same shape as `\Maeve\MaeveAutoStart`.
2. **Seed.** The session runs `gh workflow run refill.yml -f placeholders_only=true -f force=true -f batch=N` and waits for the `refill/*` PR. The workflow already skips when a refill PR is open.
3. **Resolve.** For each `NEEDS_ASIN` entry, the session drives claude-in-chrome through SiteStripe per `docs/refill-manual-resolve.md` and calls `manual_resolve.py`, which keeps its own ASIN, image-host and sponsored-name gates.
4. **Ship.** The session commits `products.json` and pushes to the `refill/*` branch. The push uses the Director's git credentials, so it fires CI. A PR opened by `GITHUB_TOKEN` does not.
5. **Merge.** The amended `automerge.yml` gate merges (see below). The session never merges.
6. **Chewy.** The session dispatches `chewy_enrich.yml` for the new topics. Before its `chewy/*` PR proceeds, the session verifies each Chewy match on Chewy's own page in Chrome (exact same product only) and the PR then auto-merges (decision 3).
7. **Digest.** After every run the session emails the Director a digest listing what was added, linked and merged, so he can see it and revert.

## Trigger: what exists

`80-workspace\startup` holds `maeve-autostart.ps1` (logon task, pre-launches Chrome, waits for the extension's native-messaging channel, then starts `claude`) and `fbmp-remote-control.ps1`. It also holds inert `register-*.CLASS3-AWAITING-APPROVAL.ps1` scripts, with a `-IUnderstandThisIsClass3` interlock. Reuse the Chrome pre-launch block and the inert-register pattern; read only, nothing run. Alternatives: a GitHub cron cannot drive the Director's logged-in Chrome, and the cloud routine cannot reach Chrome or Associates. Local Task Scheduler is the only fit. Cost: the PC must be on and logged in, which the dead-man's switch below covers.

## Permissions and the classifier

- **Recommend `--permission-mode dontAsk` with an explicit allowlist** passed inline (`--settings`), not auto mode. `dontAsk` auto-denies anything not on the list and involves no classifier `[EXTERNAL: code.claude.com/docs/en/permission-modes]`. Allowlist: `mcp__claude-in-chrome__*` (exact names to be confirmed in the spike), `Bash(gh workflow run refill.yml:*)`, `Bash(gh workflow run chewy_enrich.yml:*)`, `Bash(gh pr list/view:*)`, `Bash(python manual_resolve.py:*)`, `Bash(git add products.json)`, `Bash(git commit:*)`, `Bash(git push origin refill/*)`, `Read`. Not on it: `gh pr merge`, `.secrets`, any `main` push. The vault's PreToolUse hooks (`git-safety`, `protect-secrets`) still apply as a second layer.
- **No secrets in the prompt.** The prompt is a fixed file. `gh` authenticates from its own credential store. Impact secrets are not needed locally, because Chewy runs in Actions (`manual_resolve.py` printing "IMPACT_* not set" is expected and harmless).
- **Auto-mode classifier, verified from docs:** the classifier reads `autoMode` from `~/.claude/settings.json`, managed settings, and `--settings` inline, and never from project settings `[EXTERNAL: code.claude.com/docs/en/auto-mode-config]`. So a local `claude -p` does read the Director's user-level `autoMode` block (the vault's `claude-home\settings.json` is that file), which a cloud routine does not have (per Maeve-main: the routine API drops `auto_mode`; I did not test this). Not tested empirically here: whether the 09-24 block would recur locally. Worse, in a non-interactive `-p` run with no prompt tool, once blocks hit the thresholds (3 in a row / 20 total) the action does not run and Claude keeps working `[EXTERNAL: code.claude.com/docs/en/permission-modes]`. That is a silent failure, so the wrapper must judge success by state (branch pushed, PR exists, no placeholders left), never by exit code.

## Chrome dependency and failure path

Preconditions, checked at wrapper start and again before step 3: Chrome running, extension connected, Associates Central logged in (an expired login or 2FA prompt cannot be automated). Any failure exits nonzero, releases the lock, and opens a GitHub issue (`gh issue create`, which also emails the Director). Add a **dead-man's switch** in the style of PR #115's slot watchdog: a scheduled workflow that alerts (issue + the same SMTP email path) if no refill PR was opened or merged in the expected window. That covers a PC that was off, a dead Chrome, or a wrapper that never ran. Never silent.

## Narrow auto-merge (a new rule, separate from PR #117's gate)

Today `automerge_gate.py` requires the Claude GitHub App attribution, which a local `gh` PR does not have (it shows as plain `DMoneyOH`). PR #117 stays as it is: it is fixed and Verifier-cleared (commit 941c667) and awaits Talon. Refill PRs carry no App attribution, and PRs opened by the workflow token get no CI, so they cannot pass #117's gate. The rule below is a new, separate rule with its own tests, not an edit to #117's. It merges only when all hold: head branch `refill/*`, same repo, author `DMoneyOH`, not draft, only `products.json` changed, CI `pytest` green on the exact head SHA, and no `NEEDS_*` or `REVIEW:` in the head file. Beyond #117's checks, add a **semantic diff check**: no existing non-placeholder entry changed, and every new or filled entry has `affiliate_url == https://www.amazon.com/dp/<asin>?tag=pawpicks04-20`, image host `m.media-amazon.com`, and `chewy_url` null or `chewy.sjv.io`.
**Risk added:** author plus branch name is only as strong as the Director's `gh` token. Anything holding it can push a `refill/x` branch and merge unreviewed content to a live money path (affiliate links). The semantic check and path limit shrink that to "a wrong-but-well-formed product" and rule out redirecting a link. It still removes human review of product fit; the LLM topic-fit check and CI are the only remaining screens, and the post-run email digest lets the Director see and revert what merged. Kill switch stays the `AUTOMERGE_ENABLED` variable.

## Idempotency and limits

Skip if a `refill/*` PR is open (`refill.yml` does this already). A topic that fails twice stays `NEEDS_*`, the gate refuses to merge, and the run alerts "held topics" instead of merging around them. Batch capped (start at 4). A lock file blocks overlapping runs, and the wrapper enforces a wall-clock timeout of 60 minutes.

## Proof plan

Run one planted-fault pass on a scratch branch before enabling anything. (a) Close Chrome: expect a loud alert and no repo change. (b) Feed `manual_resolve.py` a bad image host: expect rejection. (c) Open a PR that edits an existing entry's affiliate tag, and one that leaves a `NEEDS_ASIN` in place: `automerge_gate.py --dry-run` must hold both. (d) Delay the schedule: the dead-man's switch must fire.

## Build order (Class-3 = needs Director approval and the two-clone review, Talon + Tessa)

1. Spike, Class-2: **DONE, PASSED 2026-09-25.** A headless `claude -p --chrome --permission-mode dontAsk --allowedTools "mcp__claude-in-chrome__*"` session drove Chrome, saw the SiteStripe bar and "Hello, Derek". Not tested: that `dontAsk` denies unlisted actions, and reading a fully loaded page. Findings: without `--chrome` the child has no Chrome tools, and the child must run from a directory outside the vault. Nothing scheduled.
2. New refill-PR gate rule plus tests (semantic-diff check, and coverage of the `chewy/*` PRs): **Class-3** (CI on the publish path), after #117 merges.
3. Refill dead-man's-switch workflow: **Class-3** (new CI).
4. `run-refill.ps1` and its allowlist file, inert, under `80-workspace\startup`: Class-2 for creation; **Class-3 review** because it defines the session's permissions.
5. Register the Task Scheduler task: **Class-3**, Director approves the exact command.
6. Planted-fault proof run (Class-2).
7. Director sets `AUTOMERGE_ENABLED`: his own switch, **Class-3**.
8. Chewy chain: dispatch `chewy_enrich` after #119 merges (**Class-3**), then wire it into step 6 of the flow, with the in-Chrome Chewy check and the email digest (step 7 of the flow; sending mail is a new outbound path, **Class-3**).

## Decisions (resolved 2026-09-25)

The Director decided these by voice on 2026-09-25.

1. **Host and cadence:** YES. Unattended on his own PC every other Sunday (PC on, Chrome and Amazon Associates signed in).
2. **Auto-merge of `refill/*` PRs:** YES, under the semantic-diff gate above (branch prefix, only `products.json`, CI green, no `NEEDS_`/`REVIEW` sentinels, same repo, no existing entry changed, canonical affiliate URL and tag, `m.media-amazon.com` image, `chewy.sjv.io` or null). He first accepted "human merge", then flipped; the flip is his standing answer.
3. **Chewy PRs:** also auto-merge, with the session verifying each Chewy match on Chewy's own page in Chrome first (exact same product only), plus an email digest after every run listing what was added, linked and merged, so he can see and revert.

These decisions approve the direction only. Each Class-3 activation still needs his explicit approval and the two-clone review: the gate rule, the dead-man's-switch workflow, registering the scheduled task (he approves the exact command), `AUTOMERGE_ENABLED`, and dispatching `chewy_enrich` after #119 merges.