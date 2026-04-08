#!/usr/bin/env python3
"""
test_smtp.py — Verify Gmail SMTP credentials are working.
Loads GMAIL_ACCOUNT + GMAIL_APP_PASSWORD from ~/.env and attempts auth.
Usage: python3 scripts/test_smtp.py
"""
import smtplib, os, sys
from pathlib import Path

# Load ~/.env
for line in (Path.home() / '.env').read_text().splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, _, v = line.partition('=')
        os.environ.setdefault(k.strip(), v.strip().strip('"'))

user     = os.environ.get('GMAIL_ACCOUNT', '')
password = os.environ.get('GMAIL_APP_PASSWORD', '')
sender   = os.environ.get('GMAIL_SMTP_USER', user)

print(f"Account (login):  {user}")
print(f"Sender (from):    {sender}")
print(f"Password length:  {len(password)} chars")
print(f"Testing smtp.gmail.com:587 ...")

if not user or not password:
    print("ERROR: GMAIL_ACCOUNT or GMAIL_APP_PASSWORD not set in ~/.env")
    sys.exit(1)

try:
    with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as s:
        s.ehlo()
        s.starttls()
        s.ehlo()
        s.login(user, password)
    print("SMTP OK — credentials valid")
    sys.exit(0)
except smtplib.SMTPAuthenticationError as e:
    print(f"AUTH FAIL — bad credentials or app password revoked: {e}")
    sys.exit(1)
except smtplib.SMTPException as e:
    print(f"SMTP ERROR — {e}")
    sys.exit(1)
except Exception as e:
    print(f"CONNECTION ERROR — {type(e).__name__}: {e}")
    sys.exit(1)
