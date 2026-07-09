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
import gzip
import html
import json
import os
import random
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

AFFILIATE_TAG = os.environ.get("AMAZON_PAAPI_PARTNER_TAG", "pawpicks04-20")
ASIN_RE       = re.compile(r"^B0[A-Z0-9]{8}$")
# Real product photos only -- .svg on this host is a placeholder sprite
IMAGE_HOST_RE = re.compile(
    r"^https://m\.media-amazon\.com/images/I/[^\s\"']+\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)

# Amazon Product Advertising API v5 (preferred source when keys are synced
# from the Brain to repo secrets; scrape below stays as the fallback)
PAAPI_ACCESS_KEY = os.environ.get("AMAZON_PAAPI_ACCESS_KEY", "").strip()
PAAPI_SECRET_KEY = os.environ.get("AMAZON_PAAPI_SECRET_KEY", "").strip()
PAAPI_HOST       = "webservices.amazon.com"
PAAPI_REGION     = "us-east-1"

THRESHOLD = int(os.environ.get("REFILL_THRESHOLD", "1"))
BATCH     = int(os.environ.get("REFILL_BATCH", "10"))
FORCE     = os.environ.get("FORCE_REFILL", "") == "1"

VALID_SHEETS     = ("HAPPYPET_SHEET_ID_DOGS", "HAPPYPET_SHEET_ID_CATS",
                    "HAPPYPET_SHEET_ID_HOME", "HAPPYPET_SHEET_ID_FOOD",
                    "HAPPYPET_SHEET_ID_TOYS", "HAPPYPET_SHEET_ID_HEALTH")
# Kept in sync with the categories already live in _posts/ front matter --
# the old 6-bucket set forced every non-food/non-health topic (toys, beds,
# training, grooming, tech...) into the generic "-gear" catch-all, which is
# why the queue skewed 20/23 dog-gear+cat-gear. Widened to give the ideation
# LLM a real bucket for each topic instead of defaulting to "-gear".
VALID_CATEGORIES = ("dog-gear", "dog-food", "dog-health", "dog-toys",
                    "dog-training", "dog-grooming", "dog-beds", "dog-collars",
                    "dog-crates", "dog-harnesses",
                    "cat-gear", "cat-food", "cat-health", "cat-toys",
                    "cat-litter", "cat-scratching", "cat-carriers", "cat-feeders",
                    "pet-tech", "pet-feeding")

DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
MOBILE_UA  = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")

# Try the desktop search first, then the lighter mobile endpoint -- datacenter
# IPs are often blocked on one but not the other.
SEARCH_ENDPOINTS = (
    ("https://www.amazon.com/s?k=", DESKTOP_UA),
    ("https://www.amazon.com/gp/aw/s?k=", MOBILE_UA),
)

BOT_MARKERS = ("api-services-support@amazon.com", "Robot Check",
               "Type the characters you see", "automated access")

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


# ------------------------------------------------------------- amazon pa-api

def _sigv4_headers(payload: bytes, amz_date: str) -> dict:
    """AWS Signature V4 for PA-API v5 SearchItems (stdlib hmac/hashlib)."""
    import hashlib
    import hmac
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{PAAPI_REGION}/ProductAdvertisingAPI/aws4_request"
    signed = "content-encoding;content-type;host;x-amz-date;x-amz-target"
    target = "com.amazon.paapi5.v1.ProductAdvertisingAPIv1.SearchItems"
    canonical = "\n".join([
        "POST", "/paapi5/searchitems", "",
        "content-encoding:amz-1.0",
        "content-type:application/json; charset=utf-8",
        f"host:{PAAPI_HOST}",
        f"x-amz-date:{amz_date}",
        f"x-amz-target:{target}",
        "", signed, hashlib.sha256(payload).hexdigest(),
    ])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope,
                         hashlib.sha256(canonical.encode()).hexdigest()])
    key = f"AWS4{PAAPI_SECRET_KEY}".encode()
    for part in (date_stamp, PAAPI_REGION, "ProductAdvertisingAPI", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "Content-Encoding": "amz-1.0",
        "Content-Type": "application/json; charset=utf-8",
        "X-Amz-Date": amz_date,
        "X-Amz-Target": target,
        "Authorization": (f"AWS4-HMAC-SHA256 Credential={PAAPI_ACCESS_KEY}/{scope}, "
                          f"SignedHeaders={signed}, Signature={sig}"),
    }


def _paapi_items_to_cards(data: dict) -> list:
    """Map a PA-API SearchItems response to the scrape-card shape."""
    cards = []
    for item in (data.get("SearchResult") or {}).get("Items", []):
        title = ((item.get("ItemInfo") or {}).get("Title") or {}).get("DisplayValue")
        image = (((item.get("Images") or {}).get("Primary") or {}).get("Large") or {}).get("URL")
        listings = ((item.get("Offers") or {}).get("Listings") or [])
        amount = (listings[0].get("Price") or {}).get("Amount") if listings else None
        rating = ((item.get("CustomerReviews") or {}).get("StarRating") or {}).get("Value")
        cards.append({
            "asin":  item.get("ASIN"),
            "name":  title,
            "image": image,
            "price": f"{float(amount):.2f}" if amount is not None else None,
            "stars": float(rating) if rating is not None else None,
        })
    return cards


def paapi_search(query: str) -> list:
    """
    SearchItems via PA-API v5. Returns cards or raises RuntimeError
    (missing keys, auth failure, throttle, error body).
    """
    if not PAAPI_ACCESS_KEY or not PAAPI_SECRET_KEY:
        raise RuntimeError("PA-API keys not configured")
    payload = json.dumps({
        "Keywords": query,
        "SearchIndex": "PetSupplies",
        "ItemCount": 5,
        "PartnerTag": AFFILIATE_TAG,
        "PartnerType": "Associates",
        "Marketplace": "www.amazon.com",
        "Resources": ["Images.Primary.Large", "ItemInfo.Title",
                      "Offers.Listings.Price",
                      "CustomerReviews.StarRating", "CustomerReviews.Count"],
    }).encode()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    headers = _sigv4_headers(payload, amz_date)
    req = urllib.request.Request(f"https://{PAAPI_HOST}/paapi5/searchitems",
                                 data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"PA-API HTTP {exc.code}: {exc.read().decode('utf-8','replace')[:200]}") from exc
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"PA-API request failed: {exc}") from exc
    if data.get("Errors"):
        raise RuntimeError(f"PA-API error: {data['Errors'][0].get('Message', '?')[:200]}")
    cards = _paapi_items_to_cards(data)
    if not cards:
        raise RuntimeError("PA-API returned no items")
    return cards


# ------------------------------------------------------------- amazon scrape

def _fetch_once(url: str, user_agent: str) -> str:
    """One GET with explicit gzip handling (urllib does not auto-decompress)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "") == "gzip" or raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")


def fetch_search_html(query: str) -> str:
    """
    Fetch an Amazon search page, trying desktop then mobile endpoints with
    backoff. Raises RuntimeError when every attempt is blocked or fails.
    """
    q = urllib.parse.quote_plus(query)
    errors = []
    for base, ua in SEARCH_ENDPOINTS:
        for attempt in range(2):
            try:
                body = _fetch_once(base + q, ua)
            except (urllib.error.URLError, OSError) as exc:
                errors.append(str(exc))
                time.sleep(4 + random.uniform(0, 3))
                continue
            if any(m in body for m in BOT_MARKERS) or "captcha" in body.lower()[:5000]:
                errors.append("bot-blocked (captcha page)")
                time.sleep(4 + random.uniform(0, 3))
                continue
            return body
    raise RuntimeError(f"search fetch failed after all endpoints: {errors[-1] if errors else '?'}")


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
    if results:
        return results
    # Fallback for layouts without data-component-type (e.g. mobile search):
    # any data-asin block with an amazon-CDN image + alt text. Downstream
    # validation and the LLM fit-check still gate what gets trusted.
    for m in re.finditer(r'data-asin="(B0[A-Z0-9]{8})"(.*?)(?=data-asin="B0|$)',
                         html_text, re.DOTALL):
        asin, block = m.group(1), m.group(2)
        if ">Sponsored<" in block[:4000]:
            continue
        img = re.search(r'src="(https://m\.media-amazon\.com/images/I/[^"]+)"', block)
        name = re.search(r'alt="([^"]{15,})"', block)
        price_m = re.search(r'class="a-price-whole">([\d,]+)[^0-9]*'
                            r'(?:class="a-price-fraction">(\d{2}))?', block)
        stars_m = re.search(r'([0-9.]+) out of 5 stars', block)
        if not (img and name):
            continue
        price = None
        if price_m:
            price = price_m.group(1).replace(",", "") + "." + (price_m.group(2) or "99")
        results.append({
            "asin":  asin,
            "name":  html.unescape(name.group(1)).strip(),
            "image": img.group(1),
            "price": price,
            "stars": float(stars_m.group(1)) if stars_m else None,
        })
        if len(results) >= max_results:
            break
    return results


def validate_candidate(card: dict) -> bool:
    name = card.get("name") or ""
    # Some sponsored placements (seen on the mobile search endpoint) bake
    # "Sponsored Ad - " straight into the image alt text, which sits outside
    # the surrounding-markup ">Sponsored<" / popover-class checks above.
    # Catch that case here so a paid placement never becomes the "best pick".
    if name.strip().lower().startswith("sponsored"):
        return False
    return bool(name
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
    cards = []
    if PAAPI_ACCESS_KEY and PAAPI_SECRET_KEY:
        try:
            cards = paapi_search(query)
            time.sleep(1.2)  # PA-API default throttle is 1 request/second
        except RuntimeError as exc:
            log(f"'{query}': {exc} -- falling back to scrape", "WARN")
    if not cards:
        try:
            body = fetch_search_html(query)
            cards = parse_search_results(body)
            if not any(validate_candidate(c) for c in cards):
                asin_hits = len(re.findall(r'data-asin="B0', body))
                log(f"'{query}': no parseable product cards "
                    f"(body {len(body)} chars, {asin_hits} asin attrs, "
                    f"head: {body[:120].replace(chr(10), ' ')!r})", "WARN")
        except RuntimeError as exc:
            log(f"'{query}': {exc}", "WARN")
            return None
    valid = [c for c in cards if validate_candidate(c)]
    if not valid:
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


def chewy_enrich(name: str, upc: str | None = None) -> dict:
    empty = {"chewy_url": None, "chewy_price": None,
             "chewy_stock": None, "chewy_rating": None}
    try:
        from chewy_lookup import lookup, ChewyAPIError
    except ImportError:
        return empty
    try:
        r = lookup(name, upc)
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
    if resolved.get("upc"):
        entry["upc"] = resolved["upc"]
    entry.update(chewy_enrich(resolved["name"], resolved.get("upc")))


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
- category must be the MOST SPECIFIC fit available (e.g. "dog-toys" for a toy topic,
  "cat-litter" for a litter topic) -- only fall back to the generic "dog-gear"/"cat-gear"
  bucket when nothing more specific applies. Do not let more than 2-3 of the {batch}
  topics share the same category -- spread them across toys, training, grooming, beds,
  collars/harnesses, crates, litter, scratching, carriers, feeders, tech, and feeding,
  not just gear/food/health.
- topical_sheet must fit the species
- amazon_search_query: what a shopper would type into Amazon to find the single best mainstream product for this topic
- Favor products relevant in {month} and the coming two months (seasonality), mixed across species, category, and price points.
Answer strictly as JSON."""
    # 2.5-flash spends thinking tokens from the same budget; 4096 truncated
    raw = gp._call_gemini(gp.GEMINI_GEN_MODEL, prompt, max_tokens=16384,
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
