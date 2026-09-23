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
import urllib.parse
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


# ---------------------------------------------------------------------------
# A published post's OWN Chewy link
# ---------------------------------------------------------------------------
# products.json is a rolling queue of the topics still to be WRITTEN -- four
# entries as of 2026-09-23. Reading chewy_url from it meant a post that had
# already rotated out of the queue resolved to chewy_url "" and was logged
# "SKIP -- no Chewy URL (Amazon-only)". Eight published posts carry a live
# Chewy link in their front matter and none of the four queue entries is among
# them, so on 2026-09-22 a wrong live Chewy link was found by hand, by the
# Director, on a site this job reported clean. The post's own front matter is
# what it renders from, so that is the source of truth here; the queue is a
# fallback for a topic not yet published.

# The Impact wrapper carries the real destination inside its own `u=` parameter,
# so the listing is readable from the string without contacting anybody.
_WRAPPER_HOSTS = ("chewy.sjv.io",)
_CHEWY_PRODUCT_PATH_RE = re.compile(r"^/(?P<slug>[^/]+)/dp/(?P<dp>[A-Za-z0-9]+)")

# Anchors that are a call to action rather than a product name. The FortiFlora
# post's first affiliate anchor is "Buy on Amazon"; taking that as the product
# name would judge every link against the word "Buy".
_GENERIC_ANCHOR_RE = re.compile(
    r"^\s*(buy|shop|check|view|see|get|order|click|read)\b|^\s*(here|this|link)\s*$",
    re.IGNORECASE)

# Every status this job can report. DEFERRED is the one that keeps the throttle
# honest: a post whose API check is queued for a later run is not OK and is not
# a skip, and saying so is the whole difference from the gap above.
STATUSES = ("OK", "CLEAN", "DEFERRED", "NO_URL", "UNWRAPPED", "MALFORMED",
            "VARIANT_MISMATCH", "MISMATCH", "REVIEW", "ERROR")

# Impact lookups per scheduled run. The job runs weekly, so at 12 a site of 49
# linked posts is swept in five weeks; pushing all 49 through on every run is
# the naive alternative and it scales straight into the API's rate limit.
MAX_API_LOOKUPS = 12


def parse_chewy_url(chewy_url: str) -> dict:
    """Decompose a stored chewy_url offline. No network.

    shape is one of: none (empty), sentinel (a REVIEW placeholder), wrapped
    (the Impact affiliate wrapper), bare (a plain chewy.com product URL --
    works, earns nothing), malformed (anything else).
    """
    out = {"shape": "none", "product_slug": "", "dp_id": "", "prodsku": ""}
    url = (chewy_url or "").strip()
    if not url:
        return out
    if url.startswith("REVIEW"):
        out["shape"] = "sentinel"
        return out

    parts = urllib.parse.urlparse(url)
    host = parts.netloc.lower()
    if host in _WRAPPER_HOSTS:
        query = urllib.parse.parse_qs(parts.query)
        out["prodsku"] = (query.get("prodsku") or [""])[0]
        inner = urllib.parse.urlparse((query.get("u") or [""])[0])
        m = _CHEWY_PRODUCT_PATH_RE.match(inner.path)
        if not m or not inner.netloc.lower().endswith("chewy.com"):
            out["shape"] = "malformed"
            return out
        out.update(shape="wrapped", product_slug=m.group("slug"), dp_id=m.group("dp"))
        return out

    if host.endswith("chewy.com"):
        m = _CHEWY_PRODUCT_PATH_RE.match(parts.path)
        if not m:
            out["shape"] = "malformed"
            return out
        out.update(shape="bare", product_slug=m.group("slug"), dp_id=m.group("dp"))
        return out

    out["shape"] = "malformed"
    return out


def numeric_tokens(text: str) -> set:
    """The distinct numbers a string names. "2.0" and "20" are the same version
    written two ways, so digits are joined across an internal decimal point
    before the runs are read -- otherwise the dot alone reads as a difference
    and `catit-senses-20-...` false-flags against "Catit Senses 2.0"."""
    joined = re.sub(r"(?<=\d)[.,](?=\d)", "", text or "")
    return {run.lstrip("0") or "0" for run in re.findall(r"\d+", joined)}


def find_variant_mismatch(product_slug: str, product_name: str) -> str:
    """Return why a Chewy link looks like the wrong VARIANT of the right brand,
    or "" when no contradiction is visible. Offline.

    This exists because check_brand_match cannot see this class at all. All
    three wrong links found by hand on 2026-09-22 were right-brand
    (globlazer-big-modern-tower-77-in on a review of the 74in; a Catit food
    tree on a review of the Catit Multi Feeder; the wrong FortiFlora listing
    id), so the brand identity gate passed every one of them.

    The only signal that survives without contacting Chewy is a model or size
    number: the URL's own slug says 77-in, the article's product says 74in, and
    those cannot both be the product. A verdict is given ONLY when both sides
    name numbers -- a slug with no number (the common case) supports no
    conclusion, and inventing one would make this fire on correct links.

    Scope, stated so a green run is not read as more than it is: this catches
    the Globlazer link and would NOT have caught the other two. The Catit slug
    and product name agree on 2.0; the FortiFlora repair kept the same slug and
    changed only the listing id. Those need the listing itself, which is what
    the throttled Impact check below is for.
    """
    slug_numbers = numeric_tokens(product_slug)
    name_numbers = numeric_tokens(product_name)
    if not slug_numbers or not name_numbers or (slug_numbers & name_numbers):
        return ""
    return (f"variant contradiction: link slug {product_slug!r} names "
            f"{sorted(slug_numbers)}, the article's product names {sorted(name_numbers)}")


def extract_featured_product_name(body: str, affiliate_url: str) -> str:
    """The featured product's name, read off the anchor text of the post's own
    affiliate link. Front matter carries a title and a URL but no product name,
    and the title ("Best GPS Dog Trackers for Peace of Mind...") is a category,
    not something a link can be judged against."""
    if not affiliate_url:
        return ""
    for anchor in re.findall(r"\[([^\]\n]+)\]\(" + re.escape(affiliate_url) + r"\)", body):
        text = anchor.strip()
        if text and not _GENERIC_ANCHOR_RE.match(text):
            return text
    return ""


def post_entry(md_path: Path, products: dict) -> dict:
    """One published post's checkable facts: its slug, its own Chewy link, and
    the product name that link has to agree with."""
    text = md_path.read_text(encoding="utf-8")
    fm_match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    body = text[fm_match.end():] if fm_match else text
    fm = parse_frontmatter(md_path)
    slug = extract_slug(md_path)
    queued = products.get(slug, {})
    # Front matter first: it is what the page renders from, so it is the only
    # value a reader can click. The queue entry is the fallback and used to be
    # the only source -- it is kept because a queue entry for an already
    # published post still carries a value someone may regenerate from, and
    # dropping it would stop surfacing the REVIEW sentinels sitting in it.
    chewy_url = fm.get("chewy_url") or ""
    source    = "front-matter" if chewy_url else ""
    if not chewy_url and queued.get("chewy_url"):
        chewy_url = queued["chewy_url"]
        source    = "products.json"
    product_name = (extract_featured_product_name(body, fm.get("affiliate_url", ""))
                    or queued.get("name") or fm.get("title", ""))
    return {"slug": slug, "path": str(md_path), "chewy_url": chewy_url,
            "source": source, "product_name": product_name,
            "in_queue": slug in products}


def published_chewy_links(products: dict = None) -> list:
    """Every published post that carries a Chewy link, whatever its source."""
    if products is None:
        products = load_products()
    entries = [post_entry(md, products) for md in sorted(POSTS_DIR.glob("2*-*.md"))]
    return [e for e in entries if e["chewy_url"]]


def classify_offline(entry: dict) -> tuple[str, str]:
    """Verdict from the stored link alone. Returns ("CLEAN", ...) when nothing
    is wrong on the evidence available without a lookup."""
    url = entry["chewy_url"]
    parsed = parse_chewy_url(url)
    shape = parsed["shape"]
    if shape == "none":
        return "NO_URL", "no chewy_url stored"
    if shape == "sentinel":
        return "REVIEW", f"sentinel value: {url[:60]}"
    if shape == "malformed":
        return "MALFORMED", f"not a resolvable Chewy product link: {url[:80]}"
    reason = find_variant_mismatch(parsed["product_slug"], entry["product_name"])
    if reason:
        return "VARIANT_MISMATCH", f"{reason} | product: {entry['product_name'][:60]}"
    if shape == "bare":
        return "UNWRAPPED", (f"plain chewy.com link, not the Impact wrapper -- the click "
                             f"works and earns nothing: {url[:80]}")
    return "CLEAN", f"wrapper intact, slug {parsed['product_slug']!r}, sku {parsed['prodsku']}"


def api_slice(slugs: list, week: int) -> list:
    """The slugs whose Impact lookup runs THIS week. Stateless by design: the
    job checks out at depth 1 and commits nothing but products.json, so a
    cursor file would not survive between runs. Rotating on the ISO week number
    needs no state and still sweeps everything -- a throttle that revisits the
    same head of the list forever is the silent gap wearing a rate limit."""
    slugs = list(slugs)
    if len(slugs) <= MAX_API_LOOKUPS:
        return slugs
    runs = -(-len(slugs) // MAX_API_LOOKUPS)
    start = ((week - 1) % runs) * MAX_API_LOOKUPS
    return slugs[start:start + MAX_API_LOOKUPS]


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
    entries  = [post_entry(md, products) for md in posts]
    linked   = [e for e in entries if e["chewy_url"]]

    # The Impact lookup is the only step that leaves the machine, so it is the
    # only one that gets throttled. Every offline check below runs on every
    # linked post on every run.
    week       = datetime.date.today().isocalendar()[1]
    due_slugs  = set(api_slice([e["slug"] for e in linked], week))

    log(f"Checking {len(posts)} published posts -- {len(linked)} carry a Chewy link; "
        f"Impact lookups this run (week {week}): {len(due_slugs)}/{len(linked)}")

    results = []
    counts  = {s: 0 for s in STATUSES}

    for entry in entries:
        slug         = entry["slug"]
        product_name = entry["product_name"]
        chewy_url    = entry["chewy_url"]

        status, detail = classify_offline(entry)
        if status == "CLEAN":
            # Offline checks found nothing. The stored link is still only as
            # good as the listing it points at, which needs the lookup.
            if slug in due_slugs:
                status, detail = validate_chewy_url(chewy_url, product_name)
            else:
                status = "DEFERRED"
                detail = (f"offline checks passed; Impact lookup queued for a later "
                          f"run (cap {MAX_API_LOOKUPS}/run)")

        results.append({
            "slug":         slug,
            "product_name": product_name,
            "chewy_url":    chewy_url,
            "source":       entry["source"],
            "status":       status,
            "detail":       detail,
        })
        counts[status] = counts.get(status, 0) + 1

        if status in ("OK", "CLEAN"):
            log(f"  OK    {slug}")
        elif status == "DEFERRED":
            log(f"  DEFER {slug} -- {detail}")
        elif status == "NO_URL":
            log(f"  SKIP  {slug} -- carries no Chewy link at all")
        elif status == "REVIEW":
            log(f"  REVIEW {slug} -- {detail}", "WARN")
        elif status == "UNWRAPPED":
            log(f"  UNWRAPPED {slug} -- {detail}", "ERROR")
        elif status == "MALFORMED":
            log(f"  MALFORMED {slug} -- {detail}", "ERROR")
        elif status == "VARIANT_MISMATCH":
            log(f"  VARIANT_MISMATCH {slug} -- {detail}", "ERROR")
        elif status == "MISMATCH":
            log(f"  MISMATCH {slug} -- {detail}", "ERROR")
            if args.fix and slug in products:
                products[slug]["chewy_url"]    = None
                products[slug]["chewy_price"]  = None
                products[slug]["chewy_stock"]  = None
                products[slug]["chewy_rating"] = None
                log(f"  FIX: cleared chewy_url for {slug}")
            elif args.fix:
                # --fix edits products.json and nothing else. A post whose link
                # lives only in its own front matter cannot be repaired here,
                # and saying nothing would read as repaired.
                log(f"  FIX SKIPPED {slug} -- link is in the post's front matter, "
                    f"not products.json; edit _posts/ by hand", "WARN")

    # Wrong product and lost attribution are different failures with different
    # fixes, so they are counted apart rather than folded into one number.
    blocking = (counts["MISMATCH"] + counts["VARIANT_MISMATCH"]
                + counts["UNWRAPPED"] + counts["MALFORMED"] + counts["ERROR"])

    summary = {
        "date":       datetime.date.today().isoformat(),
        "week":       week,
        "total":      len(posts),
        "linked":     len(linked),
        "api_checked": len(due_slugs),
        "counts":     counts,
        "results":    results,
    }

    log("DONE -- " + ", ".join(f"{counts[s]} {s}" for s in STATUSES if counts[s]))

    if args.fix and counts["MISMATCH"] > 0:
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
        log(f"FIX: products.json updated -- {counts['MISMATCH']} bad chewy_url entries cleared")

    if not args.report_only:
        report_path = REPO_DIR / f"chewy_link_validation_{datetime.date.today().isoformat()}.json"
        report_path.write_text(json.dumps(summary, indent=2))
        log(f"Report written: {report_path.name}")

    # Non-zero on errors too: an import failure or API outage previously left
    # this job green while validating nothing. A DEFERRED post is not a failure
    # -- its offline checks passed and its lookup is scheduled.
    if blocking > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
