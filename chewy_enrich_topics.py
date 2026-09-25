#!/usr/bin/env python3
"""
chewy_enrich_topics.py -- on-demand Chewy enrichment for products.json.

Run by .github/workflows/chewy_enrich.yml (workflow_dispatch) on a runner that
holds the Impact secrets; also runnable locally. Reuses refill_products'
chewy_enrich_detail -> chewy_lookup.lookup, so the matching logic lives in one
place. Adds the Director's rule (2026-09-25) on top: link Chewy ONLY when Chewy
sells the SAME product. A same-brand different product is no match, and
anything uncertain leaves the four chewy_* fields null. Nothing is invented.

Per selected entry, exactly one outcome:
  matched          lookup() auto-accepted it (GTIN match, or score + brand +
                   word-coverage gates) and no size/model number contradicts
                   the Amazon name -> chewy_url/price/stock/rating written.
  variant_mismatch auto-accepted by lookup() but both names carry numbers and
                   none agree (25 lb vs 40 lb) -> left null.
  candidate        lookup() returned "REVIEW:<url>" (low confidence, brand
                   conflict or low coverage) -> left null; the candidate shows
                   in the report for a human and is never written.
  not_found        lookup() returned the bare "REVIEW" sentinel -> left null.
  unavailable      credentials missing, API error, or no URL -> left null.
  skipped          named topic that already has a chewy_url -> untouched.

"matched" means "passed the automated gates", not "verified identical": the
report lists the Chewy product name beside the Amazon name for the human
reviewing the PR, who is the final exactness gate for name-only matches.

Usage:
    python3 chewy_enrich_topics.py --topics "slug-a,slug-b" --report report.md
    python3 chewy_enrich_topics.py --report report.md    # every eligible entry

Exit: 0 ok (including "nothing to do"); 1 every lookup was unavailable (bad or
missing Impact credentials -- fail loudly, no silent empty PR); 2 bad input.
Env: IMPACT_ACCOUNT_SID / IMPACT_AUTH_TOKEN (read by chewy_lookup; never printed).
"""

import argparse
import re
import sys
from pathlib import Path

import refill_products as rp
from json_io import atomic_write_json
from validate_published_chewy_links import find_variant_mismatch

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class UnknownTopic(ValueError):
    pass


def parse_topics(raw: str) -> list:
    topics = [t.strip() for t in (raw or "").split(",") if t.strip()]
    for t in topics:
        if not _SLUG_RE.match(t):
            raise ValueError(f"not a topic slug: {t!r}")
    return topics


def _eligible(entry: dict) -> bool:
    asin = entry.get("asin") or ""
    return bool(asin) and asin != "NEEDS_ASIN" and not entry.get("chewy_url")


def _select(products: list, topics: list) -> list:
    if not topics:
        return [e for e in products if _eligible(e)]
    by_topic = {e.get("topic"): e for e in products}
    missing = [t for t in topics if t not in by_topic]
    if missing:
        raise UnknownTopic(f"not in products.json: {', '.join(missing)}")
    return [by_topic[t] for t in topics]


def _classify(entry: dict, fields: dict, matched_name) -> dict:
    url = fields.get("chewy_url")
    row = {"topic": entry.get("topic"), "name": entry.get("name"),
           "matched_name": matched_name, "candidate_url": None}
    if not url:
        row["outcome"] = "unavailable"
    elif url == "REVIEW":
        row["outcome"] = "not_found"
    elif url.startswith("REVIEW:"):
        row["outcome"] = "candidate"
        row["candidate_url"] = url[len("REVIEW:"):]
    elif find_variant_mismatch(matched_name or "", entry.get("name") or ""):
        row["outcome"] = "variant_mismatch"
    else:
        row["outcome"] = "matched"
    return row


def enrich(products: list, topics: list) -> list:
    """Mutate `products` in place for passing matches only; return one report
    row per selected entry."""
    report = []
    for entry in _select(products, topics):
        if entry.get("chewy_url"):
            report.append({"topic": entry.get("topic"), "name": entry.get("name"),
                           "outcome": "skipped", "matched_name": None,
                           "candidate_url": None})
            continue
        fields, matched_name = rp.chewy_enrich_detail(entry["name"], entry.get("upc"))
        row = _classify(entry, fields, matched_name)
        if row["outcome"] == "matched":
            entry.update(fields)
        report.append(row)
    return report


def render_report(report: list) -> str:
    lines = ["Chewy enrichment (chewy_enrich.yml). Rule: link Chewy only when it sells the "
             "SAME product; same-brand different product = no link.", ""]
    matched = [r for r in report if r["outcome"] == "matched"]
    if matched:
        lines += ["**Written -- verify each is the same product before merging:**", "",
                  "| Topic | Amazon product | Chewy product matched |", "|---|---|---|"]
        lines += [f"| `{r['topic']}` | {r['name']} | {r['matched_name']} |" for r in matched]
        lines.append("")
    others = [r for r in report if r["outcome"] != "matched"]
    if others:
        lines += ["**Left null (no Chewy link):**", "",
                  "| Topic | Outcome | Chewy product seen |", "|---|---|---|"]
        for r in others:
            seen = r["matched_name"] or ""
            if r["candidate_url"]:
                seen += f" (candidate, unverified: {r['candidate_url']})"
            lines.append(f"| `{r['topic']}` | {r['outcome']} | {seen} |")
        lines.append("")
    if not report:
        lines.append("No eligible entries (asin present, chewy_url empty).")
    lines.append("_Auto-generated by chewy_enrich.yml_")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--topics", default="",
                        help="comma-separated topic slugs; empty = every eligible entry")
    parser.add_argument("--report", required=True, help="path to write the markdown report")
    args = parser.parse_args(argv)
    try:
        topics = parse_topics(args.topics)
        products = rp.load_products()
        report = enrich(products, topics)
    except ValueError as exc:  # includes UnknownTopic
        print(f"chewy_enrich_topics: {exc}", file=sys.stderr)
        return 2
    Path(args.report).write_text(render_report(report), encoding="utf-8")
    for r in report:
        print(f"{r['topic']}: {r['outcome']}")
    attempted = [r for r in report if r["outcome"] != "skipped"]
    if attempted and all(r["outcome"] == "unavailable" for r in attempted):
        print("chewy_enrich_topics: every lookup was unavailable -- check the Impact "
              "credentials/API; products.json left unchanged", file=sys.stderr)
        return 1
    if any(r["outcome"] == "matched" for r in report):
        atomic_write_json(rp.PRODUCTS_PATH, products, trailing_newline=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
