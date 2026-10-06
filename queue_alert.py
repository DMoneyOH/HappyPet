#!/usr/bin/env python3
"""queue_alert.py -- the ONE definition of queue depth and the ONE alert sender,
shared by push_pins_to_sheets' queue-low email and refill_watchdog, so the two
alerts can never disagree about how many topics are left.

Depth counts only PUBLISHABLE unpublished entries (validate_product passes): a
queue of held NEEDS_* placeholders posts nothing, so it is not depth.

Standard library plus generate_posts only (no gspread, no vault), so the watchdog
runs on a bare runner.
"""
import os
import smtplib
from email.mime.text import MIMEText

import generate_posts as gp

ALERT_TO = "hello@happypetproductreviews.com"
ALERT_FROM = "hello@happypetproductreviews.com"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def publishable_unpublished(products: dict | None = None, used: set | None = None) -> list:
    """Slugs, in queue order, that the next Stage-1 runs can actually publish."""
    products = gp.load_products() if products is None else products
    used = gp.build_used_slugs() if used is None else used
    return [slug for slug, p in products.items()
            if slug not in used and not gp.validate_product(slug, p)]


def send_alert(subject: str, body: str) -> tuple[bool, str]:
    """Send one alert email. Never raises; returns (sent, what happened). An empty
    or unset GMAIL_SMTP_USER falls back to ALERT_FROM, so From is never empty."""
    sender = os.environ.get("GMAIL_SMTP_USER") or ALERT_FROM
    login = os.environ.get("GMAIL_ACCOUNT") or sender
    password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not password:
        return False, "GMAIL_APP_PASSWORD not set -- alert email NOT sent"
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ALERT_TO
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(login, password)
            s.sendmail(sender, [ALERT_TO], msg.as_string())
    except Exception as exc:  # report, never mask
        return False, f"Alert email failed: {type(exc).__name__}: {exc}"
    return True, f"Alert email sent to {ALERT_TO}"
