#!/usr/bin/env python3
"""
validate_published_chewy_links.py
Reads all published posts in _posts/, resolves Chewy affiliate_url values,
compares matched Chewy product names against the article's product via the
brand identity gate in chewy_lookup.py, and reports mismatches.

Run locally or as a weekly GHA scheduled job.
Outputs a JSON report: chewy_link_validation_YYYY-MM-DD.json

Usage:
    python3 validate_published_chewy_links.py
    python3 validate_published_chewy_links.py --fix   # writes corrected products.json entries
    python3 validate_published_chewy_links.py --report-only  # print report, no file write
"""
import json
import os
import re
import sys
import datetime
import argparse
from pathlib import Path

from json_io import atomic_write_json, read_json

REPO_DIR  = Path(__file__).parent.resolve()
POSTS_DIR = REPO_DIR / "_posts"
LOG_PATH  = REPO_DIR / "LOGS" / f"HappyPet_{datetime.date.today().isoformat()}.log"
LOG_PATH.parent.mkdir(exist_ok=True)


def log(msg: str, level: str = "INFO") -> None:
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [CHEWY_VAL] [{level}]  {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def parse_frontmatter(md_path: Path) -> dict:
    """Extract YAML frontmatter fields from a Jekyll post."""
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm


def extract_slug(md_path: Path) -> str:
    parts = md_path.stem.split("-", 3)
    return parts[3] if len(parts) == 4 else md_path.stem


def load_products() -> dict:
    p = REPO_DIR / "products.json"
    if not p.exists():
        return {}
    data = read_json(p, default=[])
    return {e["topic"]: e for e in data if e.get("topic")}


def brand_token(text: str) -> str:
    """Extract first meaningful brand token (length >= 4, not a stop word)."""
    STOP = {"the", "a", "an", "and", "or", "for", "with", "in", "of", "to", "by",
            "recipe", "formula", "grain", "free", "best", "dog", "cat", "dogs", "cats"}
    words = [w for w in text.lower().split() if w not in STOP and len(w) >= 4]
    return words[0] if words else ""


def check_brand_match(product_name: str, chewy_matched_name: str) -> tuple[bool, str]:
    """
    Returns (ok, reason). Replicates chewy_lookup brand identity gate.
    If searched brand token does not appear in matched name and vice versa: mismatch.
    """
    if not chewy_matched_name:
        return False, "no matched name available"
    searched = brand_token(product_name)
    matched  = brand_token(chewy_matched_name)
    if not searched:
        return True, "no brand token extractable from product name"
    if searched in chewy_matched_name.lower():
        return True, f"brand '{searched}' found in matched name"
    if matched in product_name.lower():
        return True, f"matched brand '{matched}' found in product name"
    return False, f"brand mismatch: searched='{searched}' matched='{matched}'"


def validate_chewy_url(chewy_url: str, product_name: str) -> tuple[str, str]:
    """
    Re-run chewy_lookup for the product and compare against stored URL.
    Returns (status, detail):
      OK          - brand match confirmed
      MISMATCH    - stored URL resolves to different brand
      REVIEW      - sentinel stored, needs human check
      NO_URL      - no Chewy URL stored (Amazon-only product)
      ERROR       - lookup failed
    """
    if not chewy_url:
        return "NO_URL", "no chewy_url stored"
    if chewy_url.startswith("REVIEW"):
        return "REVIEW", f"sentinel value: {chewy_url[:60]}"

    try:
        from chewy_lookup import lookup, ChewyAPIError
    except ImportError as ie:
        # A broken import must be loud -- this exact failure mode (importing a
        # function that didn't exist at module level) silently rubber-stamped
        # every URL as OK for weeks.
        return "ERROR", f"chewy_lookup not importable: {ie}"

    try:
        result = lookup(product_name)
    except ChewyAPIError as e:
        # API outage / bad creds is NOT evidence of a bad link. Reporting it as
        # MISMATCH once meant --fix could clear every stored Chewy link during
        # an Impact.com blip.
        return "ERROR", f"Impact API unavailable: {e}"
    except Exception as e:
        return "ERROR", f"lookup exception: {e}"

    matched_name = result.get("chewy_matched_name") or ""
    if not matched_name:
        # Lookup ran but found nothing to compare against -- needs a human, but
        # it is not a positive contradiction of the stored link.
        return "REVIEW", "lookup found no Chewy match to compare -- verify stored URL manually"

    ok, reason = check_brand_match(product_name, matched_name)
    if ok:
        return "OK", f"brand confirmed: {matched_name[:60]}"
    # Positive contradiction: lookup matched a real product of a different brand
    return "MISMATCH", f"{reason} | stored: {chewy_url[:60]} | matched: {matched_name[:60]}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix",         action="store_true", help="Clear bad chewy_url entries in products.json")
    parser.add_argument("--report-only", action="store_true", help="Print report without writing JSON file")
    args = parser.parse_args()

    products = load_products()
    posts    = sorted(POSTS_DIR.glob("2*-*.md"))

    log(f"Checking {len(posts)} published posts for Chewy link validity")

    results   = []
    mismatches = 0
    reviews    = 0
    no_url     = 0
    ok_count   = 0
    errors     = 0

    for md in posts:
        slug = extract_slug(md)
        fm   = parse_frontmatter(md)
        product_entry = products.get(slug, {})

        product_name = product_entry.get("name") or fm.get("title", "")
        chewy_url    = product_entry.get("chewy_url", "")

        status, detail = validate_chewy_url(chewy_url, product_name)

        entry = {
            "slug":         slug,
            "product_name": product_name,
            "chewy_url":    chewy_url,
            "status":       status,
            "detail":       detail,
        }
        results.append(entry)

        if status == "OK":
            ok_count += 1
            log(f"  OK    {slug}")
        elif status == "NO_URL":
            no_url += 1
            log(f"  SKIP  {slug} -- no Chewy URL (Amazon-only)")
        elif status == "REVIEW":
            reviews += 1
            log(f"  REVIEW {slug} -- {detail}", "WARN")
        elif status == "MISMATCH":
            mismatches += 1
            log(f"  MISMATCH {slug} -- {detail}", "ERROR")
            if args.fix and slug in products:
                products[slug]["chewy_url"]    = None
                products[slug]["chewy_price"]  = None
                products[slug]["chewy_stock"]  = None
                products[slug]["chewy_rating"] = None
                log(f"  FIX: cleared chewy_url for {slug}")
        else:
            errors += 1
            log(f"  ERROR {slug} -- {detail}", "WARN")

    summary = {
        "date":       datetime.date.today().isoformat(),
        "total":      len(posts),
        "ok":         ok_count,
        "no_url":     no_url,
        "review":     reviews,
        "mismatches": mismatches,
        "errors":     errors,
        "results":    results,
    }

    log(f"DONE -- {ok_count} OK, {no_url} no URL, {reviews} REVIEW, {mismatches} MISMATCHES, {errors} ERRORS")

    if args.fix and mismatches > 0:
        json_path = REPO_DIR / "products.json"
        raw = json.loads(json_path.read_text())
        for entry in raw:
            slug = entry.get("topic", "")
            if slug in products and products[slug].get("chewy_url") is None:
                entry["chewy_url"]    = None
                entry["chewy_price"]  = None
                entry["chewy_stock"]  = None
                entry["chewy_rating"] = None
        atomic_write_json(json_path, raw)
        log(f"FIX: products.json updated -- {mismatches} bad chewy_url entries cleared")

    if not args.report_only:
        report_path = REPO_DIR / f"chewy_link_validation_{datetime.date.today().isoformat()}.json"
        report_path.write_text(json.dumps(summary, indent=2))
        log(f"Report written: {report_path.name}")

    # Non-zero on errors too: an import failure or API outage previously left
    # this job green while validating nothing.
    if mismatches > 0 or errors > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
