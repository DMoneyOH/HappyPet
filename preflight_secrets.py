#!/usr/bin/env python3
"""
preflight_secrets.py
One-shot readiness check for the HappyPet pipeline secrets. Run via the
Preflight workflow (workflow_dispatch) before go-live, or locally with the
same env vars exported.

For every secret the pipeline needs it reports one of:
    PASS  - present and, where a free auth ping exists, verified live
    WARN  - present but only presence could be checked (no safe ping)
    FAIL  - missing/empty, or the auth ping was rejected

Pings are chosen to be free and side-effect-free: model/catalog listings,
auth-info endpoints, read-only sheet opens, SMTP login without send. IFTTT
has no side-effect-free endpoint (any webhook call fires the applet), so it
is presence-only by design.

Exit code: 0 if nothing FAILed, 1 otherwise. Writes a markdown table to
$GITHUB_STEP_SUMMARY when set.
"""
import base64
import json
import os
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 20

results = []  # (name, status, detail)


def record(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail))
    print(f"  {status:4}  {name:26} {detail}", flush=True)


def http_get(url: str, headers: dict | None = None) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def check_presence(name: str) -> str:
    """Return the value if non-empty, else record FAIL and return ''."""
    val = os.environ.get(name, "").strip()
    if not val:
        record(name, "FAIL", "secret missing or empty")
    return val


def check_gemini() -> None:
    key = check_presence("GEMINI_API_KEY")
    if not key:
        return
    status, body = http_get(
        "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1&key="
        + urllib.parse.quote(key))
    if status == 200:
        record("GEMINI_API_KEY", "PASS",
               "key accepted (note: ping proves auth, not billed tier)")
    else:
        record("GEMINI_API_KEY", "FAIL", f"models list HTTP {status}: {body[:120]}")


def check_openrouter() -> None:
    key = check_presence("OPENROUTER_API_KEY")
    if not key:
        return
    status, body = http_get("https://openrouter.ai/api/v1/auth/key",
                            {"Authorization": f"Bearer {key}"})
    if status != 200:
        record("OPENROUTER_API_KEY", "FAIL", f"auth/key HTTP {status}: {body[:120]}")
        return
    try:
        data = json.loads(body).get("data", {})
        usage = data.get("usage")
        limit = data.get("limit")  # None = unlimited/credit-based
        free_tier = data.get("is_free_tier")
        detail = f"key valid; usage=${usage}, limit={'none' if limit is None else f'${limit}'}, free_tier={free_tier}"
        if free_tier:
            record("OPENROUTER_API_KEY", "FAIL",
                   detail + " -- free tier cannot run the paid Haiku reviewer; add credits")
        else:
            record("OPENROUTER_API_KEY", "PASS", detail)
    except (json.JSONDecodeError, AttributeError) as exc:
        record("OPENROUTER_API_KEY", "WARN", f"key valid but response unparseable: {exc}")


def check_groq() -> None:
    key = check_presence("GROQ_API_KEY")
    if not key:
        return
    status, body = http_get("https://api.groq.com/openai/v1/models",
                            {"Authorization": f"Bearer {key}"})
    if status == 200:
        record("GROQ_API_KEY", "PASS", "key accepted (legacy fallback only)")
    else:
        record("GROQ_API_KEY", "WARN",
               f"models HTTP {status} -- legacy fallback key, not required for go-live")


def check_cse() -> None:
    key = check_presence("GOOGLE_CSE_KEY")
    cx = check_presence("GOOGLE_CSE_CX")
    if not key or not cx:
        return
    status, body = http_get(
        "https://www.googleapis.com/customsearch/v1?q=test&num=1"
        f"&key={urllib.parse.quote(key)}&cx={urllib.parse.quote(cx)}")
    if status == 200:
        record("GOOGLE_CSE_KEY+CX", "PASS", "test query OK (1 of 100 free daily queries)")
    else:
        record("GOOGLE_CSE_KEY+CX", "FAIL", f"test query HTTP {status}: {body[:120]}")


def check_impact() -> None:
    sid = check_presence("IMPACT_ACCOUNT_SID")
    token = check_presence("IMPACT_AUTH_TOKEN")
    if not sid or not token:
        return
    auth = base64.b64encode(f"{sid}:{token}".encode()).decode()
    status, body = http_get(
        f"https://api.impact.com/Mediapartners/{urllib.parse.quote(sid)}/Campaigns?PageSize=1",
        {"Authorization": f"Basic {auth}", "Accept": "application/json"})
    if status == 200:
        record("IMPACT_SID+TOKEN", "PASS", "authenticated campaign read OK (Chewy leg)")
    else:
        record("IMPACT_SID+TOKEN", "FAIL", f"campaigns HTTP {status}: {body[:120]}")


def check_sheets() -> None:
    # Topical sheet IDs: presence only (opening all six adds no signal beyond
    # the auth + share check below, and the SA may legitimately lack some).
    for name in ("HAPPYPET_SHEET_ID_DOGS", "HAPPYPET_SHEET_ID_CATS",
                 "HAPPYPET_SHEET_ID_HOME", "HAPPYPET_SHEET_ID_FOOD",
                 "HAPPYPET_SHEET_ID_TOYS", "HAPPYPET_SHEET_ID_HEALTH"):
        if os.environ.get(name, "").strip():
            record(name, "PASS", "present (presence-only)")
        else:
            record(name, "FAIL", "secret missing or empty")

    key_b64 = check_presence("GCP_SA_KEY_B64")
    fb_sheet = check_presence("FACEBOOK_QUEUE_SHEET_ID")
    if not key_b64:
        return
    try:
        info = json.loads(base64.b64decode(key_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        record("GCP_SA_KEY_B64", "FAIL", f"not valid base64 JSON: {exc}")
        return
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets",
                          "https://www.googleapis.com/auth/drive"])
        gc = gspread.Client(auth=creds)
        record("GCP_SA_KEY_B64", "PASS", f"service account authenticated ({info.get('client_email','?')})")
        if fb_sheet:
            sh = gc.open_by_key(fb_sheet)
            ws = sh.get_worksheet(0)
            record("FACEBOOK_QUEUE_SHEET_ID", "PASS",
                   f"FB Queue sheet opened read-only: '{sh.title}', {ws.row_count} rows")
    except Exception as exc:  # gspread raises many types; any means not ready
        target = "FACEBOOK_QUEUE_SHEET_ID" if fb_sheet else "GCP_SA_KEY_B64"
        record(target, "FAIL", f"{type(exc).__name__}: {exc}")


def check_gmail() -> None:
    user = check_presence("GMAIL_SMTP_USER")
    pw = check_presence("GMAIL_APP_PASSWORD")
    if os.environ.get("GMAIL_ACCOUNT", "").strip():
        record("GMAIL_ACCOUNT", "PASS", "present (presence-only)")
    else:
        record("GMAIL_ACCOUNT", "FAIL", "secret missing or empty")
    if not user or not pw:
        return
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=TIMEOUT) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(user, pw)
        record("GMAIL_SMTP_USER+PASSWORD", "PASS", "SMTP login OK (no email sent)")
    except smtplib.SMTPAuthenticationError as exc:
        record("GMAIL_SMTP_USER+PASSWORD", "FAIL", f"SMTP auth rejected: {exc.smtp_code}")
    except OSError as exc:
        record("GMAIL_SMTP_USER+PASSWORD", "WARN", f"SMTP unreachable from runner: {exc}")


def check_ifttt() -> None:
    if os.environ.get("IFTTT_MAKER_KEY", "").strip():
        record("IFTTT_MAKER_KEY", "WARN",
               "present -- NOT pinged (any webhook call fires a real pin); "
               "verified live during the supervised go-live run")
    else:
        record("IFTTT_MAKER_KEY", "FAIL", "secret missing or empty")


def check_facebook() -> None:
    token = check_presence("FACEBOOK_PAGE_TOKEN")
    if not token:
        return
    status, body = http_get(
        "https://graph.facebook.com/debug_token?input_token="
        f"{urllib.parse.quote(token)}&access_token={urllib.parse.quote(token)}")
    if status == 200 and json.loads(body).get("data", {}).get("is_valid"):
        record("FACEBOOK_PAGE_TOKEN", "PASS", "token valid per debug_token")
    else:
        record("FACEBOOK_PAGE_TOKEN", "FAIL", f"debug_token HTTP {status}: {body[:120]}")


def main() -> None:
    print("HappyPet secrets preflight\n" + "=" * 60, flush=True)
    check_gemini()
    check_openrouter()
    check_groq()
    check_cse()
    check_impact()
    check_sheets()
    check_gmail()
    check_ifttt()
    check_facebook()

    fails = [r for r in results if r[1] == "FAIL"]
    warns = [r for r in results if r[1] == "WARN"]
    print("=" * 60)
    print(f"RESULT: {len(results)} checks -- "
          f"{len(results) - len(fails) - len(warns)} PASS, {len(warns)} WARN, {len(fails)} FAIL")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
        with open(summary_path, "a") as f:
            f.write("## Secrets preflight\n\n| Check | Status | Detail |\n|---|---|---|\n")
            for name, status, detail in results:
                f.write(f"| `{name}` | {icon[status]} {status} | {detail} |\n")
            f.write(f"\n**{len(fails)} FAIL / {len(warns)} WARN / "
                    f"{len(results) - len(fails) - len(warns)} PASS**\n")

    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
