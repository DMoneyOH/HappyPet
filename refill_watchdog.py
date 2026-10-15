#!/usr/bin/env python3
"""refill_watchdog.py -- the refill dead-man's switch (run by refill-watchdog.yml).

Keyed on queue DEPTH, not on whether a refill job ran, so it catches every cause
at once: the PC was off, Chrome was dead, the wrapper never started, a refill PR
is sitting held, or nobody merged it. Depth is queue_alert.publishable_unpublished,
the same count push_pins_to_sheets' queue-low check uses.

  ALERT when fewer than MIN_DEPTH publishable topics are queued (an open refill PR
        is named in the alert, but does not silence it: it is not merged yet), or
        when the open-PR list could not be read (fail closed).

Env: OPEN_REFILL_PRS, the JSON list of open refill/* PR numbers the workflow read
with gh (anything else is "unreadable"). The alert is written to GITHUB_OUTPUT as
`summary` (one line, no counts, for a title that dedups) and `body`; the
workflow's next step opens the GitHub issue.
Exit 0 healthy; exit 1 after alerting, so the run goes red and GitHub's own
failed-run notice is a second channel even when the issue cannot be opened.
"""
import json
import os
import sys
import uuid

import queue_alert

MIN_DEPTH = 2


def parse_open_prs(raw) -> list | None:
    """[PR numbers] from the workflow's JSON list, or None when unreadable."""
    try:
        prs = json.loads(raw or "")
    except ValueError:
        return None
    if not isinstance(prs, list) or not all(
            type(n) is int and n > 0 for n in prs):   # type(): bool is an int
        return None
    return prs


def verdict(depth: int, open_prs: list | None) -> str | None:
    """The alert reason, or None when the queue is healthy."""
    if open_prs is None:
        return f"open refill PRs could not be read; {depth} publishable topic(s) queued"
    if depth < MIN_DEPTH:
        held = (f"refill PR(s) open but not merged: "
                + ", ".join(f"#{n}" for n in open_prs)) if open_prs else "no refill PR is open"
        return f"only {depth} publishable topic(s) queued; {held}"
    return None


def main() -> int:
    depth = len(queue_alert.publishable_unpublished())
    prs = parse_open_prs(os.environ.get("OPEN_REFILL_PRS"))
    reason = verdict(depth, prs)
    if reason is None:
        print(f"healthy: {depth} publishable topic(s) queued")
        return 0
    print(f"ALERT: {reason}")
    repo = os.environ.get("GITHUB_REPOSITORY", "DMoneyOH/HappyPet")
    run = f"Run: https://github.com/{repo}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '?')}"
    if prs is None:
        # The count may be fine; what failed is the PR listing, so say how to fix that.
        summary = "open refill PRs could not be read"
        todo = (f"The \"Count open refill PRs\" step could not list the open refill/* PRs, "
                f"so the watchdog cannot tell whether a refill is waiting. Read that step's "
                f"log, fix the gh call or its token, then re-run this workflow. If fewer than "
                f"{MIN_DEPTH} topics are queued, also run a refill.\n\n"
                f"Close this issue once a watchdog run passes.")
    else:
        summary = "the topic queue is low"
        links = "".join(f"\nOpen refill PR: https://github.com/{repo}/pull/{n}" for n in prs)
        todo = (f"The Mon/Thu publish runs out when the queue does. Run a refill, or merge "
                f"the open refill PR.{links}\n\nClose this issue once the queue is refilled.")
    body = f"Refill watchdog: {reason}.\n\n{run}\n\n{todo}"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        end = f"EOF_{uuid.uuid4().hex}"   # a delimiter the body cannot contain
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"summary={summary}\nbody<<{end}\n{body}\n{end}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
