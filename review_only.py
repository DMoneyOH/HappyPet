#!/usr/bin/env python3
"""
review_only.py — Standalone one-time reviewer for a single article.
Reads article from _posts/, runs rule-based pre-screen, then Gemini 1.5 Flash review.
NO git, NO publish, NO sheet writes. Read + report only.

Usage: python3 review_only.py [slug]
Default slug: best-dog-crates
"""
import os, re, json, sys, time, urllib.request, urllib.error, datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────
REPO_DIR       = Path(__file__).parent.resolve()
REVIEWER_MODEL = "models/gemini-2.5-flash-lite"
GEMINI_URL     = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
MAX_RETRIES    = 3

BANNED_CLICHES = [
    "delve", "it's worth noting", "in conclusion", "look no further",
    "game-changer", "comprehensive guide", "navigate",
]
EM_DASH        = "\u2014"
AFFILIATE_PAT  = re.compile(r"amzn\.to/\S+")
DISCLOSURE_PAT = re.compile(r"(affiliate|commission|earn|sponsored)", re.IGNORECASE)

# ── Helpers ─────────────────────────────────────────────────────────────────
def banner(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def load_env() -> str:
    env_path = Path.home() / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("ERROR: GEMINI_API_KEY not set in ~/.env")
        sys.exit(1)
    return key

def find_article(slug: str) -> tuple:
    """Returns (filepath, title, keyword, content) or exits."""
    matches = list(REPO_DIR.glob(f"_posts/*{slug}*.md"))
    if not matches:
        print(f"ERROR: No article found matching slug '{slug}'")
        sys.exit(1)
    fpath   = matches[0]
    raw     = fpath.read_text(encoding="utf-8")
    # Parse front matter
    title   = ""
    keyword = ""
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            for line in fm.splitlines():
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
                if line.startswith("keyword:") or line.startswith("description:"):
                    keyword = line.split(":", 1)[1].strip().strip('"')
            content = parts[2].strip()
        else:
            content = raw
    else:
        content = raw
    return fpath, title, keyword, content, raw

# ── Rule-based pre-screen ────────────────────────────────────────────────────
def pre_screen(content: str) -> dict:
    results = {"hard_fails": [], "warnings": [], "info": {}}

    # Word count
    words = len(content.split())
    results["info"]["word_count"] = words
    if words < 800:
        results["hard_fails"].append(f"Word count too low: {words} words (minimum 800)")

    # Affiliate link
    aff_matches = AFFILIATE_PAT.findall(content)
    results["info"]["affiliate_links"] = aff_matches
    if not aff_matches:
        results["hard_fails"].append("No affiliate link found (amzn.to missing)")

    # Disclosure
    disc_match = DISCLOSURE_PAT.search(content)
    results["info"]["disclosure_found"] = bool(disc_match)
    if not disc_match:
        results["hard_fails"].append("No affiliate disclosure text found")

    # Banned clichés
    found_cliches = [c for c in BANNED_CLICHES if c.lower() in content.lower()]
    results["info"]["cliches_found"] = found_cliches
    if found_cliches:
        results["hard_fails"].append(f"Banned clichés detected: {found_cliches}")

    # Em dash warning
    em_count = content.count(EM_DASH)
    results["info"]["em_dash_count"] = em_count
    if em_count > 0:
        results["warnings"].append(f"Em dash found {em_count} time(s) — consider replacing with commas or restructuring")

    return results

# ── Gemini 1.5 Flash reviewer ────────────────────────────────────────────────
def call_reviewer(title: str, keyword: str, content: str, api_key: str) -> dict:
    prompt = f"""You are a senior human editor for Happy Pet Product Reviews.
A DIFFERENT AI (Gemini 2.5 Flash) wrote this article. Your job is to critique it objectively.

ARTICLE TITLE: {title}
FOCUS KEYWORD: {keyword}

FULL ARTICLE:
---
{content}
---

Return ONLY a single valid JSON object. No preamble, no markdown fences.
Begin with {{ and end with }}.

Required format:
{{"pass": true/false, "scores": {{"human_voice": 1-5, "warmth": 1-5, "readability": 1-5, "accuracy": 1-5}}, "flags": ["brief issue description, max 15 words each"], "rewrite_instructions": "specific fixes needed or empty string if pass"}}

PASS criteria: all scores >= 3, no fabricated product specs, reads like a human wrote it.
Be harsh. Flag anything AI-generated, robotic, or filler. Keep each flag under 15 words."""

    payload = json.dumps({
        "model": REVIEWER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2048,
        "temperature": 0.2,
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(GEMINI_URL, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = json.loads(resp.read())["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            raw = re.sub(r"^```json\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 30 * (2 ** attempt)
                print(f"  429 rate limit — waiting {wait}s (attempt {attempt}/{MAX_RETRIES})")
                time.sleep(wait)
            else:
                return {"error": f"HTTP {exc.code}: {exc.reason}"}
        except json.JSONDecodeError as exc:
            return {"error": f"JSON parse failed: {exc}", "raw": raw}
        except Exception as exc:
            return {"error": str(exc)}
    return {"error": "Failed after max retries"}

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    slug    = sys.argv[1] if len(sys.argv) > 1 else "best-dog-crates"
    api_key = load_env()

    banner(f"REVIEW ONLY — {slug}")
    print(f"Reviewer model : {REVIEWER_MODEL}")
    print(f"Article        : _posts/*{slug}*.md")
    print(f"Timestamp      : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # Load article
    fpath, title, keyword, content, raw = find_article(slug)
    print(f"\nFile           : {fpath.name}")
    print(f"Title          : {title}")
    print(f"Keyword        : {keyword}")

    # ── BEFORE: Pre-screen ──────────────────────────────────────────────────
    banner("BEFORE — Rule-Based Pre-Screen (Python, zero API calls)")
    ps = pre_screen(content)

    print(f"\n  Word count     : {ps['info']['word_count']}")
    print(f"  Affiliate links: {ps['info']['affiliate_links'] or 'NONE FOUND'}")
    print(f"  Disclosure     : {'✓ found' if ps['info']['disclosure_found'] else '✗ MISSING'}")
    print(f"  Clichés found  : {ps['info']['cliches_found'] or 'none'}")
    print(f"  Em dashes      : {ps['info']['em_dash_count']}")

    if ps["hard_fails"]:
        print(f"\n  HARD FAILS ({len(ps['hard_fails'])}):")
        for f in ps["hard_fails"]:
            print(f"    ✗ {f}")
    else:
        print("\n  ✓ All hard checks passed")

    if ps["warnings"]:
        print(f"\n  WARNINGS ({len(ps['warnings'])}):")
        for w in ps["warnings"]:
            print(f"    ⚠ {w}")

    pre_screen_passed = len(ps["hard_fails"]) == 0

    # ── AFTER: AI Reviewer ──────────────────────────────────────────────────
    banner("AFTER — Gemini 1.5 Flash AI Review")

    if not pre_screen_passed:
        print("\n  ⛔ Pre-screen FAILED — skipping AI reviewer call")
        print("  Fix hard fails above before running AI review.")
    else:
        print("\n  Pre-screen passed. Calling Gemini 1.5 Flash reviewer...")
        print("  (30s pre-sleep to avoid 429...)")
        time.sleep(30)
        result = call_reviewer(title, keyword, content, api_key)

        if "error" in result:
            print(f"\n  ERROR from reviewer: {result['error']}")
            if "raw" in result:
                print(f"  Raw response: {result['raw'][:500]}")
        else:
            verdict = "✓ PASS" if result.get("pass") else "✗ FAIL"
            print(f"\n  Verdict        : {verdict}")
            scores  = result.get("scores", {})
            print(f"  Scores         :")
            for k, v in scores.items():
                bar = "█" * v + "░" * (5 - v)
                print(f"    {k:<20} {bar} {v}/5")
            flags = result.get("flags", [])
            if flags:
                print(f"\n  Flags ({len(flags)}):")
                for f in flags:
                    print(f"    • {f}")
            else:
                print("\n  No flags raised.")
            instructions = result.get("rewrite_instructions", "")
            if instructions:
                print(f"\n  Rewrite instructions:\n    {instructions}")

    banner("DONE — no files written, no publish, no git")

if __name__ == "__main__":
    main()
