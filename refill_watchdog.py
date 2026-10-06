#!/usr/bin/env python3
"""refill_watchdog.py -- the refill dead-man's switch (run by refill-watchdog.yml).

Keyed on queue DEPTH, not on whether a refill job ran, so it catches every cause
at once: the PC was off, Chrome was dead, the wrapper never started, a refill PR
is sitting held, or nobody merged it.

Counts only PUBLISHABLE unpublished entries (validate_product passes): a queue of
held NEEDS_* placeholders is not a healthy queue.

  ALERT when 0 publishable entries remain (the next Mon/Thu run posts nothing,
        whatever PR is open), or
        when fewer than MIN_DEPTH remain and no refill/* PR is open, or
        when the open-PR count could not be read (fail closed).

Env: OPEN_REFILL_PRS (the count the workflow read with gh; anything that is not a
non-negative integer is "unreadable"), and for the email GMAIL_SMTP_USER,
GMAIL_ACCOUNT, GMAIL_APP_PASSWORD. Exit 0 healthy; exit 1 after alerting, so the
run goes red and GitHub's own failed-run notice is a second channel even when the
email cannot be sent.
"""
import os
import smtplib
import sys
from email.mime.text import MIMEText

import generate_posts as gp

MIN_DEPTH = 2
ALERT_TO = "hello@happypetproductreviews.com"
RUN_URL = "https://github.com/{repo}/actions/runs/{run}"


def publishable_unpublished(products: dict, used: set) -> list:
    return [slug for slug, p in products.items()
            if slug not in used and not gp.validate_product(slug, p)]


def parse_open_count(raw) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def verdict(depth: int, open_prs: int | None) -> str | None:
    """The alert reason, or None when the queue is healthy."""
    if open_prs is None:
        return f"open refill PRs could not be read; {depth} publishable topic(s) queued"
    if depth == 0:
        return f"no publishable topic is queued ({open_prs} refill PR(s) open, none merged)"
    if depth < MIN_DEPTH and open_prs == 0:
        return f"only {depth} publishable topic(s) queued and no refill PR is open"
    return None


def send_alert(reason: str, run_url: str) -> bool:
    user = os.environ.get("GMAIL_SMTP_USER", "")
    login = os.environ.get("GMAIL_ACCOUNT", "")
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        print("GMAIL_APP_PASSWORD not set -- alert email NOT sent")
        return False
    msg = MIMEText(f"Refill watchdog: {reason}.\n\nThe Mon/Thu publish runs out when the "
                   f"queue does. Run a refill (or merge the open refill PR).\n\nRun: {run_url}")
    msg["Subject"] = f"[HappyPet] Refill watchdog: {reason}"
    msg["From"] = user
    msg["To"] = ALERT_TO
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
            s.starttls()
            s.login(login, password)
            s.sendmail(user, [ALERT_TO], msg.as_string())
    except Exception as exc:  # report, never mask: the run still exits red
        print(f"Alert email failed: {type(exc).__name__}: {exc}")
        return False
    print("Alert email sent")
    return True


def main() -> int:
    products = gp.load_products()
    depth = len(publishable_unpublished(products, gp.build_used_slugs()))
    reason = verdict(depth, parse_open_count(os.environ.get("OPEN_REFILL_PRS")))
    if reason is None:
        print(f"healthy: {depth} publishable topic(s) queued")
        return 0
    print(f"ALERT: {reason}")
    send_alert(reason, RUN_URL.format(repo=os.environ.get("GITHUB_REPOSITORY", "DMoneyOH/HappyPet"),
                                      run=os.environ.get("GITHUB_RUN_ID", "?")))
    return 1


if __name__ == "__main__":
    sys.exit(main())
