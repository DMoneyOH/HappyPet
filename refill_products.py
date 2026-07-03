#!/usr/bin/env python3
"""
refill_products.py -- Stage 0: keep the topic queue fed.

When the number of unpublished topics in products.json drops to
REFILL_THRESHOLD (default 1), this script:

  1. Backfills any existing NEEDS_ASIN / NEEDS_IMAGE placeholder entries by
     resolving a real product from an Amazon search.
  2. Asks Gemini for up to REFILL_BATCH new topic candidates (deduped against
     every published and queued slug), then resolves each the same way.
  3. Appends the new entries to products.json and writes REFILL_RESULT.json.

Amazon resolution is scrape-with-hold: a search-results fetch parsed for the
first organic product card. Every resolved product must pass hard validation
(ASIN shape, image host) plus a cheap LLM fit-check before it is trusted;
anything that fails keeps its NEEDS_* placeholders, which the generator's
validate_product() already refuses to publish. The workflow opens a PR, so a
human reviews every entry before it enters the queue.

Affiliate links are long-form: https://www.amazon.com/dp/<ASIN>?tag=pawpicks04-20

Env:
  REFILL_THRESHOLD  refill when unpublished count <= this (default 1)
  REFILL_BATCH      max new topics per run (default 10)
  FORCE_REFILL      "1" bypasses the threshold gate (manual dispatch)
  GEMINI_API_KEY    topic ideation + product fit-check
  IMPACT_*          optional Chewy enrichment via chewy_lookup
"""
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import generate_posts as gp

REPO_DIR      = Path(__file__).parent.resolve()
PRODUCTS_PATH = REPO_DIR / "products.json"
RESULT_PATH   = REPO_DIR / "REFILL_RESULT.json"

AFFILIATE_TAG = "pawpicks04-20"
ASIN_RE       = re.compile(r"^B0[A-Z0-9]{8}$")
IMAGE_HOST_RE = re.compile(r"^https://m\.media-amazon\.com/images/I/[^\s\"']+$")

THRESHOLD = int(os.environ.get("REFILL_THRESHOLD", "1"))
BATCH     = int(os.environ.get("REFILL_BATCH", "10"))
FORCE     = os.environ.get("FORCE_REFILL", "") == "1"

VALID_SHEETS     = ("HAPPYPET_SHEET_ID_DOGS", "HAPPYPET_SHEET_ID_CATS",
                    "HAPPYPET_SHEET_ID_HOME", "HAPPYPET_SHEET_ID_FOOD",
                    "HAPPYPET_SHEET_ID_TOYS", "HAPPYPET_SHEET_ID_HEALTH")
VALID_CATEGORIES = ("dog-gear", "dog-food", "dog-health",
                    "cat-gear", "cat-food", "cat-health")

SEARCH_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic":               {"type": "string"},
                    "title":               {"type": "string"},
                    "keyword":             {"type": "string"},
                    "species":             {"type": "string", "enum": ["dog", "cat", "both"]},
                    "category":            {"type": "string", "enum": list(VALID_CATEGORIES)},
                    "topical_sheet":       {"type": "string", "enum": list(VALID_SHEETS)},
                    "amazon_search_query": {"type": "string"},
                },
                "required": ["topic", "title", "keyword", "species", "category",
                             "topical_sheet", "amazon_search_query"],
            },
        },
    },
    "required": ["topics"],
}

FIT_SCHEMA = {
    "type": "object",
    "properties": {"fits": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["fits", "reason"],
}


def log(msg: str, level: str = "INFO") -> None:
    gp.log(f"[REFILL] {msg}", level)


# ---------------------------------------------------------------- queue state

def load_products() -> list:
    return json.loads(PRODUCTS_PATH.read_text()) if PRODUCTS_PATH.exists() else []


def published_slugs() -> set:
    """Slugs of every dated or DRAFT post (a DRAFT is spoken-for)."""
    slugs = set()
    for md in (REPO_DIR / "_posts").glob("*.md"):
        slug = gp.slug_from_post_stem(md.stem)
        if slug:
            slugs.add(slug)
    return slugs


def unpublished_count(products: list, pub: set) -> int:
    return sum(1 for e in products if e.get("topic") not in pub)


# ------------------------------------------------------------- amazon scrape

def fetch_search_html(query: str) -> str:
    """Fetch an Amazon search page. Raises RuntimeError on block/failure."""
    url = "https://www.amazon.com/s?k=" + urllib.parse.quote_plus(query)
    req = urllib.request.Request(url, headers=SEARCH_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"search fetch failed: {exc}") from exc
    if "api-services-support@amazon.com" in body or "captcha" in body.lower()[:5000]:
        raise RuntimeError("bot-blocked (captcha page)")
    return body


def parse_search_results(html_text: str, max_results: int = 8) -> list:
    """
    Extract organic product cards from Amazon search HTML.
    Returns [{asin, name, image, price, stars}] in page order; every field
    except asin/name may be None. Sponsored cards are skipped when detectable.
    """
    results = []
    # Each organic result card is a div with data-asin and data-component-type
    for m in re.finditer(
            r'data-asin="(B0[A-Z0-9]{8})"[^>]*data-component-type="s-search-result"(.*?)'
            r'(?=data-asin="B0|$)', html_text, re.DOTALL):
        asin, block = m.group(1), m.group(2)
        if 'class="puis-label-popover-default"' in block[:4000] or ">Sponsored<" in block[:4000]:
            continue
        img = re.search(r'src="(https://m\.media-amazon\.com/images/I/[^"]+)"', block)
        # Title: alt text of the product image is the cleanest single source
        name = re.search(r'alt="([^"]{15,})"', block)
        price_m = re.search(r'class="a-price-whole">([\d,]+)[^0-9]*'
                            r'(?:class="a-price-fraction">(\d{2}))?', block)
        stars_m = re.search(r'([0-9.]+) out of 5 stars', block)
        price = None
        if price_m:
            price = price_m.group(1).replace(",", "") + "." + (price_m.group(2) or "99")
        results.append({
            "asin":  asin,
            "name":  html.unescape(name.group(1)).strip() if name else None,
            "image": img.group(1) if img else None,
            "price": price,
            "stars": float(stars_m.group(1)) if stars_m else None,
        })
        if len(results) >= max_results:
            break
    return results


def validate_candidate(card: dict) -> bool:
    return bool(card.get("name")
                and ASIN_RE.match(card.get("asin") or "")
                and card.get("image") and IMAGE_HOST_RE.match(card["image"]))


def llm_fit_check(topic_title: str, product_name: str) -> bool:
    """Cheap yes/no: does this product actually belong in this roundup?"""
    prompt = (f'Article topic: "{topic_title}"\nProduct: "{product_name}"\n'
              'Is this product a sensible primary pick for that article topic? '
              'Answer strictly as JSON.')
    try:
        raw = gp._call_gemini(gp.FACTCHECK_MODEL, prompt, max_tokens=200,
                              temperature=0.0, label="refill-fit",
                              log_fn=log, json_schema=FIT_SCHEMA)
        verdict = json.loads(raw)
        if not verdict["fits"]:
            log(f"fit-check rejected '{product_name[:60]}': {verdict['reason'][:100]}", "WARN")
        return bool(verdict["fits"])
    except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
        # Fit-check unavailable: fail safe -- hold rather than trust unverified
        log(f"fit-check unavailable ({exc}) -- holding candidate", "WARN")
        return False


def first_brand_word(name: str) -> str:
    return name.split()[0].lower() if name and name.split() else ""


def resolve_product(topic_title: str, query: str) -> dict | None:
    """
    Search Amazon and return {name, asin, image, price, stars, runners_up}
    for the first verified card, or None (caller keeps placeholders).
    """
    try:
        cards = parse_search_results(fetch_search_html(query))
    except RuntimeError as exc:
        log(f"'{query}': {exc}", "WARN")
        return None
    valid = [c for c in cards if validate_candidate(c)]
    if not valid:
        log(f"'{query}': no parseable product cards", "WARN")
        return None
    top = valid[0]
    if not llm_fit_check(topic_title, top["name"]):
        return None
    top_brand = first_brand_word(top["name"])
    runners = []
    for c in valid[1:]:
        if first_brand_word(c["name"]) not in {top_brand, *map(first_brand_word, runners)}:
            runners.append(c["name"])
        if len(runners) == 2:
            break
    return {**top, "runners_up": "; ".join(runners)}


def chewy_enrich(name: str) -> dict:
    empty = {"chewy_url": None, "chewy_price": None,
             "chewy_stock": None, "chewy_rating": None}
    try:
        from chewy_lookup import lookup, ChewyAPIError
    except ImportError:
        return empty
    try:
        r = lookup(name)
        if r.get("chewy_url"):
            return {"chewy_url": r.get("chewy_url"), "chewy_price": r.get("chewy_price"),
                    "chewy_stock": r.get("chewy_stock"), "chewy_rating": r.get("chewy_rating")}
    except ChewyAPIError as exc:
        log(f"chewy lookup unavailable for '{name[:40]}': {exc}", "WARN")
    except Exception as exc:
        log(f"chewy lookup error for '{name[:40]}': {exc}", "WARN")
    return empty


def apply_resolution(entry: dict, resolved: dict) -> None:
    entry["name"]          = resolved["name"]
    entry["asin"]          = resolved["asin"]
    entry["affiliate_url"] = f"https://www.amazon.com/dp/{resolved['asin']}?tag={AFFILIATE_TAG}"
    entry["image"]         = resolved["image"]
    entry["price"]         = resolved["price"]
    entry["stars"]         = resolved["stars"]
    if resolved.get("runners_up"):
        entry["runners_up"] = resolved["runners_up"]
    entry.update(chewy_enrich(resolved["name"]))


# ------------------------------------------------------------ topic ideation

def slugify(text: str) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9-]", "", text.lower().replace(" ", "-"))).strip("-")


def ideate_topics(pub: set, queued: set, batch: int) -> list:
    month = time.strftime("%B")
    taken = ", ".join(sorted(pub | queued))
    prompt = f"""You plan content for a pet product review blog (dogs and cats, budget-conscious US owners).
Propose exactly {batch} NEW product roundup topics we have not covered.

Already covered or queued (do NOT repeat or closely overlap any of these): {taken}

Rules:
- topic: a URL slug starting with "best-", e.g. "best-heated-cat-bed"
- title: an engaging article headline for that roundup
- keyword: the primary SEO search phrase
- species: dog, cat, or both
- category and topical_sheet must fit the species
- amazon_search_query: what a shopper would type into Amazon to find the single best mainstream product for this topic
- Favor products relevant in {month} and the coming two months (seasonality), mixed across species and price points.
Answer strictly as JSON."""
    raw = gp._call_gemini(gp.GEMINI_GEN_MODEL, prompt, max_tokens=4096,
                          temperature=0.8, label="refill-ideate",
                          log_fn=log, json_schema=TOPIC_SCHEMA)
    topics = json.loads(raw)["topics"]
    out, seen = [], set(pub) | set(queued)
    for t in topics:
        slug = slugify(t["topic"])
        if not slug.startswith("best-"):
            slug = "best-" + slug
        bare = slug.removeprefix("best-")
        # drop collisions with anything taken (either slug form)
        if any(bare == s or bare == s.removeprefix("best-") or slug == s for s in seen):
            log(f"dedup dropped candidate '{slug}' (already covered)", "WARN")
            continue
        seen.add(slug)
        t["topic"] = slug
        out.append(t)
    return out[:batch]


def build_entry(t: dict) -> dict:
    return {
        "topic":         t["topic"],
        "title":         t["title"],
        "keyword":       t["keyword"],
        "name":          f"NEEDS_ASIN placeholder for {t['keyword']}",
        "asin":          "NEEDS_ASIN",
        "affiliate_url": f"https://www.amazon.com/dp/NEEDS_ASIN?tag={AFFILIATE_TAG}",
        "image":         "NEEDS_IMAGE",
        "species":       t["species"],
        "category":      t["category"],
        "format":        "roundup",
        "topical_sheet": t["topical_sheet"],
        "stars":         None,
        "price":         None,
        "runners_up":    "",
        "chewy_url":     None, "chewy_price": None,
        "chewy_stock":   None, "chewy_rating": None,
        "amazon_search_query": t["amazon_search_query"],
    }


# ----------------------------------------------------------------------- main

def main() -> None:
    products = load_products()
    pub = published_slugs()
    count = unpublished_count(products, pub)
    log(f"queue check: {count} unpublished topic(s), threshold {THRESHOLD}"
        f"{' (FORCED)' if FORCE else ''}")
    if count > THRESHOLD and not FORCE:
        log("queue healthy -- nothing to do")
        RESULT_PATH.write_text(json.dumps(
            {"ran": False, "unpublished": count, "threshold": THRESHOLD}))
        return

    backfilled, still_held, added_resolved, added_placeholder = [], [], [], []

    # 1. Backfill existing placeholder entries
    for entry in products:
        if entry.get("asin") != "NEEDS_ASIN" and entry.get("image") != "NEEDS_IMAGE":
            continue
        query = entry.get("amazon_search_query") or entry.get("keyword") or entry.get("name", "")
        log(f"backfill: {entry['topic']} (query: '{query}')")
        resolved = resolve_product(entry.get("title", entry["topic"]), query)
        if resolved:
            apply_resolution(entry, resolved)
            backfilled.append(entry["topic"])
        else:
            still_held.append(entry["topic"])
        time.sleep(3)  # be polite to the search endpoint

    # 2. New topics
    queued = {e["topic"] for e in products} | {e["topic"].removeprefix("best-") for e in products}
    try:
        candidates = ideate_topics(pub, queued, BATCH)
    except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
        log(f"topic ideation failed: {exc}", "ERROR")
        candidates = []
    for t in candidates:
        entry = build_entry(t)
        log(f"new topic: {entry['topic']} (query: '{t['amazon_search_query']}')")
        resolved = resolve_product(t["title"], t["amazon_search_query"])
        if resolved:
            apply_resolution(entry, resolved)
            added_resolved.append(entry["topic"])
        else:
            added_placeholder.append(entry["topic"])
        products.append(entry)
        time.sleep(3)

    changed = bool(backfilled or added_resolved or added_placeholder)
    if changed:
        PRODUCTS_PATH.write_text(json.dumps(products, indent=2) + "\n")

    result = {
        "ran": True, "unpublished_before": count,
        "backfilled": backfilled, "backfill_still_held": still_held,
        "added_resolved": added_resolved, "added_placeholder": added_placeholder,
        "changed": changed,
    }
    RESULT_PATH.write_text(json.dumps(result, indent=2))
    log(f"DONE -- backfilled {len(backfilled)}, new resolved {len(added_resolved)}, "
        f"new placeholder {len(added_placeholder)}, backfill still held {len(still_held)}")

    # Honest gate: a refill that ran and resolved nothing at all is a failure
    if not backfilled and not added_resolved:
        log("resolved zero products -- Amazon scrape likely blocked; failing run", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    main()
