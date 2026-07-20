#!/usr/bin/env python3
"""
Happy Pet Product Reviews Generator v22.0 — hybrid models: Gemini 2.5 Flash generates, Claude Haiku 4.5 reviews (schema-enforced JSON)
- TOPICS derived entirely from products.json (no hardcoded list)
- products.json is single source of truth: slug, title, keyword, format, category, species, topical_sheet
- Dynamic internal links: resolved at runtime from published _posts/ by category
- Pre-publish validation gate: missing required fields = held, not published
- load_dotenv at top of main() for local runs; GHA uses env secrets directly
- Reviewer JSON parse hardened; content truncated before review prompt
- Slug-based dedup across all dates
- Pin queue staged for publish.yml -> post_pins.py
- DONE log includes held count
"""
import os, re, json, datetime, time, urllib.request, urllib.error, urllib.parse, subprocess
from pathlib import Path

from json_io import atomic_write_json, read_json

try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
except ImportError:
    DOTENV_AVAILABLE = False

try:
    import gspread
    from google.oauth2.service_account import Credentials as GCredentials
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False

try:
    from generate_pin_images import make_pin_for_post
    PIN_GEN_AVAILABLE = True
except ImportError:
    PIN_GEN_AVAILABLE = False

try:
    from chewy_lookup import lookup as chewy_lookup
    CHEWY_LOOKUP_AVAILABLE = True
except ImportError:
    CHEWY_LOOKUP_AVAILABLE = False

REPO_DIR  = Path(__file__).parent.resolve()
POSTS_DIR = REPO_DIR / "_posts"
LOG_PATH  = Path(__file__).parent / "LOGS" / f"HappyPet_{datetime.date.today().isoformat()}.log"
LOCK_PATH = Path("/tmp/happypet_gen.lock")
LOG_PATH.parent.mkdir(exist_ok=True)  # ensure LOGS/ exists

# --- Provider config ---
# Hybrid model strategy (2026-07): paid Gemini generates, Claude reviews.
# Generator:  Gemini 2.5 Flash (paid, direct API)    -> OpenRouter gpt-oss-120b:free (emergency fallback)
# Reviewer:   Claude Haiku 4.5 via OpenRouter        -> Gemini 2.5 Flash Lite (responseSchema fallback)
#             (schema-enforced JSON; routed through OpenRouter on the existing
#             OPENROUTER_API_KEY -- no direct Anthropic account needed)
# Rewriter:   call_generator/Gemini (attempt 1)      -> Gemini Flash Lite + OR fallback (attempt 2)
# Fact-check: Gemini 2.5 Flash Lite (primary)        -> OpenRouter gpt-oss-20b:free (fallback)
# Cross-family review is deliberate: a judge from the writer's own family
# inflates pass rates, and schema enforcement makes REVIEWER_JSON_ERROR
# structurally impossible instead of merely less likely.
GROQ_URL             = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL       = "https://openrouter.ai/api/v1/chat/completions"
GEMINI_GEN_MODEL     = "gemini-2.5-flash"
OR_GEN_MODEL         = "openai/gpt-oss-120b:free"
# Primary generator: Claude Sonnet via OpenRouter. Claude follows the style
# contract (no em dashes, no first-person, no fabricated stats) far more reliably
# than Gemini Flash, which the reviewer kept rejecting. Bump this slug for a newer
# Sonnet if desired -- same OpenRouter account already runs anthropic/claude-haiku-4.5.
OR_GEN_MODEL_CLAUDE  = "anthropic/claude-sonnet-4.5"
REVIEWER_MODEL       = "anthropic/claude-haiku-4.5"
REVIEWER_FALLBACK    = "gemini-2.5-flash-lite"
REVIEWER_ENABLED     = True
GEMINI_REWRITE_MODEL = "gemini-2.5-flash-lite"
OR_FACTCHECK_MODEL   = "openai/gpt-oss-20b:free"
FACTCHECK_MODEL      = "gemini-2.5-flash-lite"
OR_HEADERS_EXTRA     = {"HTTP-Referer": "https://happypetproductreviews.com", "X-Title": "HappyPetReviews"}
MAX_REVIEW_ATTEMPTS  = 3
INTER_DELAY          = 300
RPM_SLEEP            = 8
REVIEW_PRE_SLEEP     = 2
MAX_RETRIES          = 2
GITHUB_REPO      = "DMoneyOH/HappyPet"
GITHUB_ASSIGNEE  = "DMoneyOH"
SITE_BASE        = "https://happypetproductreviews.com"

# Banned phrases — apply to pin descriptions AND article body
# Keep in sync with WRITING STYLE rules in make_prompt()
# {noun} is filled per-product species: dog / cat / pet (species == both)
BANNED_PHRASE_MAP = [
    (r'pet parents',          '{noun} owners'),
    (r'pet parent',           '{noun} owner'),
    (r'furry family members', '{noun}s'),
    (r'furry family member',  '{noun}'),
    (r'furry family',         '{noun}s'),
    (r'furry friend',         '{noun}'),
    (r'fur babies',           '{noun}s'),
    (r'fur baby',             '{noun}'),
    (r'paw-some',             'great'),
    (r'put our paws',         'done the research'),
    (r'tail wagging',         'impressive'),
    (r'tail-wagging',         'impressive'),
]

# Articles 1-10 category map (predate products.json; remain hardcoded)
# Articles 11+ categories registered at runtime from products.json
SLUG_CATEGORIES = {
    "best-dog-collars-small-breeds":    "dog-collars",
    "best-cat-scratching-posts":        "cat-scratching",
    "best-no-pull-dog-harness":         "dog-harnesses",
    "best-automatic-cat-feeder":        "cat-feeders",
    "best-dog-toys-aggressive-chewers": "dog-toys",
    "best-cat-litter-odor-control":     "cat-litter",
    "best-dog-beds-large-breeds":       "dog-beds",
    "best-pet-water-fountain":          "pet-feeding",
    "best-puppy-training-pads":         "dog-training",
    "best-cat-carrier-travel":          "cat-carriers",
}

# Permanent -- covers articles 1-10 which predate products.json. Do not delete.
SLUG_TO_TOPICAL_SHEET_STATIC = {
    "best-dog-collars-small-breeds":    "HAPPYPET_SHEET_ID_TOYS",
    "best-dog-toys-aggressive-chewers": "HAPPYPET_SHEET_ID_TOYS",
    "best-no-pull-dog-harness":         "HAPPYPET_SHEET_ID_TOYS",
    "best-puppy-training-pads":         "HAPPYPET_SHEET_ID_HOME",
    "best-dog-beds-large-breeds":       "HAPPYPET_SHEET_ID_HOME",
    "best-cat-carrier-travel":          "HAPPYPET_SHEET_ID_HOME",
    "best-automatic-cat-feeder":        "HAPPYPET_SHEET_ID_HOME",
    "best-cat-scratching-posts":        "HAPPYPET_SHEET_ID_TOYS",
    "best-cat-litter-odor-control":     "HAPPYPET_SHEET_ID_HOME",
    "best-pet-water-fountain":          "HAPPYPET_SHEET_ID_HOME",
}


def log(msg: str, level: str = "INFO") -> None:
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [GENERATOR] [{level}]  {msg}"
    print(line, flush=True)
    with LOG_PATH.open('a') as f: f.write(line + chr(10))


def log_reviewer(msg: str, level: str = "INFO") -> None:
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [REVIEWER]  [{level}]  {msg}"
    print(line, flush=True)
    with LOG_PATH.open('a') as f: f.write(line + chr(10))


def log_pin(msg: str, level: str = "INFO") -> None:
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [PINGEN]    [{level}]  {msg}"
    print(line, flush=True)
    with LOG_PATH.open('a') as f: f.write(line + chr(10))



# ---------------------------------------------------------------------------
# Shared HTTP helper -- all provider calls route through here
# ---------------------------------------------------------------------------
def http_post(url, payload, headers, *, label, log_fn=None, timeout=60, retries=None,
              backoff_base=60, backoff_exp=False, passthrough_codes=frozenset()):
    if log_fn is None: log_fn = log
    if retries is None: retries = MAX_RETRIES
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors='replace')
            if exc.code in passthrough_codes:
                raise RuntimeError(label + ' HTTP ' + str(exc.code) + ': ' + body[:200])
            if exc.code in (429, 502, 503):
                if attempt == retries:
                    break  # terminal attempt: raising anyway, don't sleep first
                wait = backoff_base * (2 ** attempt if backoff_exp else attempt)
                log_fn('  ' + label + ' ' + str(exc.code) + ' attempt ' + str(attempt) + '/' + str(retries) + ' -- wait ' + str(wait) + 's', 'WARN')
                time.sleep(wait)
            else:
                raise RuntimeError(label + ' HTTP ' + str(exc.code) + ': ' + body[:200])
        except urllib.error.URLError as exc:
            if attempt == retries:
                break
            log_fn('  ' + label + ' network error attempt ' + str(attempt) + ': ' + str(exc.reason), 'WARN')
            time.sleep(RPM_SLEEP * 2)
    raise RuntimeError(label + ' exhausted after ' + str(retries) + ' attempt(s)')


def _call_gemini(model: str, prompt: str, *, max_tokens: int, temperature: float,
                 label: str, log_fn=None, json_schema: dict = None, timeout: int = 90) -> str:
    """
    Shared Gemini generateContent call. Validates the response shape and raises
    RuntimeError on any failure so callers can fail over. When json_schema is
    given, the response is constrained to schema-valid JSON (responseSchema).
    """
    if log_fn is None:
        log_fn = log
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError(f"{label}: GEMINI_API_KEY not set")
    gen_cfg = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if json_schema:
        gen_cfg["responseMimeType"] = "application/json"
        gen_cfg["responseSchema"] = json_schema
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_cfg,
    }).encode()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    raw = http_post(url, payload, {"Content-Type": "application/json"},
                    label=label, log_fn=log_fn, timeout=timeout, retries=2, backoff_base=30)
    data = json.loads(raw)
    try:
        candidate = data["candidates"][0]
        text = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"{label}: malformed Gemini response: {str(data)[:200]}") from exc
    if not text or not text.strip():
        raise RuntimeError(f"{label}: empty Gemini response")
    finish = candidate.get("finishReason", "?")
    if finish == "MAX_TOKENS":
        raise RuntimeError(f"{label}: response truncated (finishReason=MAX_TOKENS)")
    log_fn(f"  API ok ({label}/{model}): {len(text)} chars, finish={finish}")
    return text


# Reviewer scorecard schema. Enforced server-side by both reviewer paths:
# Claude via output_config.format (needs additionalProperties: false),
# Gemini via responseSchema (rejects additionalProperties -- stripped below).
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "scores": {
            "type": "object",
            "properties": {
                "human_voice": {"type": "integer"},
                "warmth": {"type": "integer"},
                "readability": {"type": "integer"},
                "accuracy": {"type": "integer"},
            },
            "required": ["human_voice", "warmth", "readability", "accuracy"],
            "additionalProperties": False,
        },
        "affiliate_link_present": {"type": "boolean"},
        "em_dash_count": {"type": "integer"},
        "ai_patterns_found": {"type": "array", "items": {"type": "string"}},
        "flags": {"type": "array", "items": {"type": "string"}},
        "rewrite_instructions": {"type": "string"},
    },
    "required": ["pass", "scores", "affiliate_link_present", "em_dash_count",
                 "ai_patterns_found", "flags", "rewrite_instructions"],
    "additionalProperties": False,
}


def _strip_additional_properties(schema):
    """Gemini's responseSchema rejects additionalProperties; Claude requires it."""
    if isinstance(schema, dict):
        return {k: _strip_additional_properties(v) for k, v in schema.items()
                if k != "additionalProperties"}
    if isinstance(schema, list):
        return [_strip_additional_properties(s) for s in schema]
    return schema


REVIEW_SCHEMA_GEMINI = _strip_additional_properties(REVIEW_SCHEMA)


def _call_openrouter_reviewer(prompt: str) -> dict:
    """
    Review via Claude Haiku 4.5 routed through OpenRouter with structured
    output (response_format json_schema, strict) -- the response text is
    schema-valid JSON, so no regex repair passes are needed. provider
    require_parameters makes OpenRouter route only to providers that honor
    response_format. Raises RuntimeError on any failure so the caller can
    fail over to the independently schema-enforced Gemini path.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    payload = json.dumps({
        "model": REVIEWER_MODEL,
        "max_tokens": 4096,
        "temperature": 0.1,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "review_scorecard",
                "strict": True,
                "schema": REVIEW_SCHEMA,
            },
        },
        "provider": {"require_parameters": True},
    }).encode()
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        **OR_HEADERS_EXTRA,
    }
    raw = http_post(OPENROUTER_URL, payload, headers, label="Reviewer-Haiku-OR",
                    log_fn=log_reviewer, timeout=90, retries=2, backoff_base=30)
    text = _extract_or_content(raw, "Reviewer-Haiku-OR")
    # Belt and braces: strict schema enforcement should make this a no-op, but
    # if a provider ever degrades it, a fence-wrapped scorecard still parses
    # and anything worse raises into the Gemini fallback instead of holding
    # the article unreviewed.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(text)


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "", s.lower().replace(" ", "-"))


def build_url(slug: str, utm: bool = False) -> str:
    category = SLUG_CATEGORIES.get(slug, "pet-accessories")
    base = f"{SITE_BASE}/{category}/{slug}/"
    if utm:
        return base + "?utm_source=pinterest&utm_medium=social&utm_campaign=pin"
    return base


def scrub_banned_phrases(text: str, species: str = "dog") -> str:
    """Deterministic banned-phrase scrub, species-aware -- a cat article must
    never end up saying 'dog owners'. Applied to pin descriptions AND the
    article body (the body was previously prompt-enforced only, and published
    posts show the prompt alone does not hold)."""
    noun = {"cat": "cat", "dog": "dog"}.get(species, "pet")
    for pattern, replacement in BANNED_PHRASE_MAP:
        text = re.sub(pattern, replacement.format(noun=noun), text, flags=re.IGNORECASE)
    return text.strip()


def clean_pin_desc(text: str, species: str = "dog") -> str:
    """Strip banned phrases from pin description before writing to queue."""
    return scrub_banned_phrases(text, species)


def extract_pin_desc(content: str, default: str) -> tuple:
    """
    Find a PIN_DESC marker in the first few lines and remove it from the body.
    Models sometimes wrap the marker ('**PIN_DESC:**') or prepend a blank line;
    a marker that survives into the published body is worse than a generic
    description, so match loosely and always strip.
    Returns (pin_desc, content_without_marker).
    """
    lines = content.split("\n")
    for idx, line in enumerate(lines[:5]):
        m = re.match(r"^\s*[*_#>`\s]*PIN_DESC:\s*[*_]*\s*(.*)$", line, re.IGNORECASE)
        if m:
            desc = m.group(1).strip().strip("*_").strip()
            del lines[idx]
            return (desc or default), "\n".join(lines).lstrip("\n")
    return default, content


def build_pin_image_url_for_queue(slug: str) -> str:
    """URL for sheet/queue entries. Includes ?v= cache-bust stamp for Pinterest CDN."""
    version = datetime.date.today().strftime("%Y%m%d")
    return f"{SITE_BASE}/assets/images/pins/{slug}.jpg?v={version}"


def build_pin_image_url_for_ifttt(slug: str) -> str:
    """URL for IFTTT Maker webhook payloads. Bare URL -- no query string.
    Pinterest CDN rejects image URLs containing query parameters."""
    return f"{SITE_BASE}/assets/images/pins/{slug}.jpg"


def enrich_with_chewy(slug: str, product: dict) -> bool:
    """
    If chewy_url is not yet set on this product, call chewy_lookup and write
    the result back into products.json. Returns True if products.json was updated.

    Sentinel rules:
      Full URL          -> written to chewy_url, button will render
      "REVIEW:{url}"   -> logged as warning, chewy_url left null, no button
      "REVIEW"         -> logged as warning, chewy_url left null, no button
      None              -> credentials missing or error, chewy_url left null
    """
    if not CHEWY_LOOKUP_AVAILABLE:
        return False

    existing = product.get("chewy_url")
    if existing and not str(existing).startswith("REVIEW"):
        return False  # already have a confirmed URL

    product_name = product.get("name", "")
    if not product_name:
        return False

    log(f"  [chewy] Looking up: {product_name}")
    try:
        result = chewy_lookup(product_name)
    except Exception as e:
        log(f"  [chewy] Lookup error for {slug}: {e}", "WARN")
        return False

    url = result.get("chewy_url")
    if not url or str(url).startswith("REVIEW"):
        log(f"  [chewy] REVIEW sentinel for {slug} -- manual verification required, no button will render", "WARN")
        return False

    # Confirmed URL -- update the in-memory product dict and write back to products.json
    matched_name = result.get("chewy_matched_name") or "unknown"
    log(f"  [chewy] Matched: '{matched_name[:70]}' -> {url[:60]}")
    product["chewy_url"]    = url
    product["chewy_price"]  = result.get("chewy_price")
    product["chewy_stock"]  = result.get("chewy_stock")
    product["chewy_rating"] = result.get("chewy_rating")

    json_path = REPO_DIR / "products.json"
    try:
        raw = json.loads(json_path.read_text())
        for entry in raw:
            if entry.get("topic") == slug:
                entry["chewy_url"]    = url
                entry["chewy_price"]  = result.get("chewy_price")
                entry["chewy_stock"]  = result.get("chewy_stock")
                entry["chewy_rating"] = result.get("chewy_rating")
                break
        atomic_write_json(json_path, raw)
        log(f"  [chewy] products.json updated for {slug}: {url[:60]}")
        return True
    except Exception as e:
        log(f"  [chewy] Failed to write products.json for {slug}: {e}", "WARN")
        return False


def load_products() -> dict:
    """
    Load products.json keyed by topic slug.
    Registers each product category into SLUG_CATEGORIES for URL generation.
    products.json is the single source of truth for all article metadata.
    """
    p = REPO_DIR / "products.json"
    if not p.exists():
        log(f"products.json not found", "WARN")
        return {}
    data = read_json(p)
    if isinstance(data, list):
        result = {}
        for entry in data:
            slug = entry.get("topic")
            if not slug:
                continue
            if entry.get("category"):
                SLUG_CATEGORIES[slug] = entry["category"]
            result[slug] = entry
        return result
    return data


def slug_from_post_stem(stem: str) -> str | None:
    """
    Extract the topic slug from a _posts/ filename stem.
    Handles both shapes: 'YYYY-MM-DD-slug' (published) and 'DRAFT-slug' (pending).
    Returns None for anything else.
    """
    if stem.startswith("DRAFT-"):
        return stem[len("DRAFT-"):] or None
    parts = stem.split("-", 3)
    if len(parts) == 4:
        return parts[3]
    return None


def build_used_slugs() -> set:
    """Scan _posts/ and return set of used slugs -- published AND pending drafts,
    so a draft awaiting Stage 2 is never regenerated (and overwritten)."""
    used = set()
    for md in POSTS_DIR.glob("*.md"):
        slug = slug_from_post_stem(md.stem)
        if slug:
            used.add(slug)
    return used


def find_related_published_slug(current_slug: str, current_category: str) -> tuple:
    """
    Find best internal link target at runtime from published _posts/.
    Scoring: same category = 3, same category prefix = 2, any published = 1.
    Returns (url, anchor_text) or (None, None) if _posts/ is empty.
    """
    candidates = []
    for md in POSTS_DIR.glob("*.md"):
        if md.stem.startswith("DRAFT-"):
            continue  # drafts have no live URL to link to
        parts = md.stem.split("-", 3)
        if len(parts) != 4:
            continue
        slug = parts[3]
        if slug == current_slug:
            continue
        cat = SLUG_CATEGORIES.get(slug, "")
        score = 1
        if cat == current_category:
            score = 3
        elif cat.split("-")[0] == current_category.split("-")[0]:
            score = 2
        candidates.append((score, slug))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: -x[0])
    best_slug = candidates[0][1]
    anchor = best_slug.replace("best-", "").replace("-", " ")
    return build_url(best_slug), anchor


def validate_product(slug: str, product: dict) -> list:
    """
    Returns list of error strings. Empty = valid. Non-empty = hold article.
    Runs before any API call so we never waste tokens on a bad entry.
    """
    errors = []
    if not product:
        errors.append(f"No product entry in products.json for slug: {slug}")
        return errors
    for field in ("affiliate_url", "name", "species", "title", "keyword", "category", "format", "image"):
        if not product.get(field):
            errors.append(f"Missing required field: {field}")
    if product.get("image") == "NEEDS_IMAGE":
        errors.append("image URL not sourced (NEEDS_IMAGE) -- use SiteStripe to get the URL")
    if product.get("asin") == "NEEDS_ASIN" or "NEEDS_ASIN" in product.get("affiliate_url", ""):
        errors.append("ASIN not resolved (NEEDS_ASIN) -- affiliate link would be dead; resolve via manual_resolve.py")
    return errors


class GenerationStageError(Exception):
    """Raised when a pipeline stage produces output that fails its contract.
    GHA captures the non-zero exit and logs the message. Never swallowed silently."""
    pass


# Accepted affiliate-link shapes in a published article body: short amzn.to links
# OR tag-bearing amazon.com/dp links. refill_products.py emits the long-form
# amazon.com/dp/{asin}?tag={tag} shape; both are valid Amazon Associates links.
AFFILIATE_LINK_RE = re.compile(r"https?://(?:amzn\.to/\S+|(?:www\.)?amazon\.com/dp/[A-Za-z0-9]+)")


def validate_output(stage: str, content: str, slug: str, affiliate_url: str = "") -> None:
    """
    Output contract gate. Raises GenerationStageError on violation.
    Called after each pipeline stage. GHA step captures non-zero exit.

    At the post-review gate the affiliate link must be present. When the entry's
    exact `affiliate_url` is supplied, it must appear verbatim in the body (this
    also catches a wrong/hallucinated link); otherwise any recognized affiliate
    link shape (amzn.to or amazon.com/dp) satisfies the gate.
    """
    MIN_WORD_COUNT   = 700
    MIN_CHARS        = 2000

    if not content or not content.strip():
        raise GenerationStageError(f"[{stage}] {slug}: empty output")
    if len(content) < MIN_CHARS:
        raise GenerationStageError(
            f"[{stage}] {slug}: output too short ({len(content)} chars, min {MIN_CHARS})"
        )
    word_count = len(content.split())
    if word_count < MIN_WORD_COUNT:
        raise GenerationStageError(
            f"[{stage}] {slug}: word count too low ({word_count} words, min {MIN_WORD_COUNT})"
        )
    # Affiliate link required only at Gate 2 (post-review); rewrite has a chance to inject it first
    if stage == "review":
        if affiliate_url:
            if affiliate_url not in content:
                raise GenerationStageError(
                    f"[{stage}] {slug}: expected affiliate link not found in output (looked for {affiliate_url})"
                )
        elif not AFFILIATE_LINK_RE.search(content):
            raise GenerationStageError(
                f"[{stage}] {slug}: no affiliate link (amzn.to or amazon.com/dp) found in output"
            )


# Minimum reviewer scores for a pass. Enforced in code (evaluate_scorecard), not
# just stated in the reviewer prompt -- a miscalibrated LLM can return pass=true
# with low scores, and we must not publish on its say-so alone.
REVIEW_SCORE_MINIMUMS = {"human_voice": 4, "warmth": 4, "readability": 3, "accuracy": 3}


def evaluate_scorecard(scorecard: dict, content: str) -> tuple:
    """
    Decide pass/fail from a reviewer scorecard WITHOUT trusting the model's
    self-reported `pass`, numeric scores, or em-dash count on their own.

    Deterministic overrides (any one fails the article):
      - every numeric score must clear its minimum (REVIEW_SCORE_MINIMUMS)
      - an em dash present in the actual body (checked here, not the model's count)
      - a reported em_dash_count > 0
      - accuracy/fabrication keywords in the flags

    Returns (passed: bool, flags: list).
    """
    passed = bool(scorecard.get("pass", False))
    flags  = list(scorecard.get("flags", []))
    scores = scorecard.get("scores", {}) or {}

    # Numeric thresholds enforced in code, not just in the prompt
    for key, minimum in REVIEW_SCORE_MINIMUMS.items():
        val = scores.get(key)
        if not isinstance(val, (int, float)) or val < minimum:
            if passed:
                flags.append(f"{key}={val} below minimum {minimum}")
            passed = False

    # Em dashes: trust the real body, never the model's self-reported count alone
    if "—" in content:
        if passed:
            flags.append("em_dash_in_body")
        passed = False
    if scorecard.get("em_dash_count", 0) and scorecard["em_dash_count"] > 0:
        passed = False

    # Accuracy/fabrication flags always fail regardless of pass=true
    accuracy_keywords = ("fabricat", "unverif", "invent", "statistic", "percentag",
                         "specific number", "no source", "not verif", "made up",
                         "cited", "claimed", "without source")
    if flags and any(kw in " ".join(str(f) for f in flags).lower() for kw in accuracy_keywords):
        passed = False

    return passed, flags


def call_generator(prompt: str, api_key: str) -> str:
    """
    Generator call chain:
      1. Claude Sonnet via OpenRouter (paid) -- primary
      2. Gemini 2.5 Flash (direct API) -- emergency fallback only
    Claude clears the anti-AI reviewer bar (no em dashes / first-person /
    fabricated stats) that Gemini Flash consistently failed; Gemini stays as a
    safety net for when OpenRouter is unavailable. Raises if both are exhausted.
    """
    # --- Tier 1: Claude Sonnet via OpenRouter (primary) ---
    or_gen_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if or_gen_key:
        try:
            payload = json.dumps({
                "model": OR_GEN_MODEL_CLAUDE,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 8192,
                "temperature": 0.7,
            }).encode()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {or_gen_key}",
                **OR_HEADERS_EXTRA,
            }
            raw = http_post(OPENROUTER_URL, payload, headers, label="Gen-Claude-Sonnet-OR",
                            timeout=120, retries=2, backoff_base=30)
            return _extract_or_content(raw, f"Gen {OR_GEN_MODEL_CLAUDE}")
        except Exception as exc:
            log(f"  Claude Sonnet generator failed: {exc} -- failing over to Gemini", "WARN")
    else:
        log("  OPENROUTER_API_KEY not set -- using Gemini generator directly", "WARN")

    # --- Tier 2: Gemini 2.5 Flash (emergency fallback) ---
    return _call_gemini(GEMINI_GEN_MODEL, prompt, max_tokens=8192, temperature=0.75,
                        label="Gemini-Gen-Fallback")


def _extract_or_content(raw: bytes, label: str) -> str:
    """
    Validate an OpenRouter chat-completions response and return message content.
    Raises RuntimeError on any malformed/degenerate shape so callers can fail over.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{label}: non-JSON response: {exc}") from exc
    if "error" in data:
        raise RuntimeError(f"{label}: API error body: {str(data['error'])[:200]}")
    choices = data.get("choices")
    if not choices:
        raise RuntimeError(f"{label}: no choices in response")
    content = choices[0].get("message", {}).get("content")
    if not content:
        raise RuntimeError(f"{label}: null/empty message content")
    finish = choices[0].get("finish_reason", "?")
    tokens = data.get("usage", {}).get("completion_tokens", "?")
    if finish == "length":
        # Truncated mid-article: at 8192 max_tokens a legitimate article never
        # hits this, so treat it as a failure and let the caller fail over
        # instead of burning fact-check + review spend on a cut-off draft
        raise RuntimeError(f"{label}: response truncated (finish_reason=length, {tokens} tokens)")
    log(f"  API ok ({label}): {len(content)} chars, {tokens} tokens, finish={finish}")
    return content

def make_review_prompt(title: str, keyword: str, content: str) -> str:
    """
    Full 21-category AI writing audit built from the avoid-ai-writing catalog
    and beautiful-prose style contract. Replaces the 6-pattern legacy prompt.
    """
    content_sample = content[:15000] if len(content) > 15000 else content
    return f"""You are a senior human editor for Happy Pet Product Reviews, a budget-focused pet product affiliate blog.
Your job is to score this article honestly and catch every AI writing pattern it contains. Do not be generous.

This article was written by an AI. Score 4 or 5 ONLY if you would genuinely not suspect AI wrote it.

ARTICLE TITLE: {title}
FOCUS KEYWORD: {keyword}

ARTICLE CONTENT:
---
{content_sample}
---

=== SCORING RUBRIC ===

human_voice:
  5 = Real opinion, specific details, natural imperfection, distinct point of view.
  4 = Mostly natural. Minor stiffness in 1-2 places.
  3 = Competent but generic — could be written by anyone or anything.
  2 = Noticeably AI-patterned — feature lists, generic transitions, no personality.
  1 = Pure marketing copy or feature dump. No human presence.

warmth:
  5 = Advice from a knowledgeable friend who owns pets.
  4 = Friendly but slightly distant.
  3 = Neutral. Informative but transactional.
  2 = Clinical or detached.
  1 = Cold, robotic, or condescending.

readability:
  5 = Flows effortlessly. Varied sentence length. Zero re-reading required.
  4 = Good flow with 1-2 awkward spots.
  3 = Readable but some sentences need a second pass.
  2 = Frequent long or convoluted sentences.
  1 = Difficult to follow.

accuracy:
  5 = All claims verifiable or appropriately hedged.
  4 = One minor unverified but plausible detail.
  3 = Some soft assertions — plausible but not grounded.
  2 = Multiple unverified specs presented as fact.
  1 = Significant factual errors or invented specifications.

=== 21-CATEGORY AI PATTERN AUDIT ===

Check every category. Flag every violation found.

1. FORMATTING — Em dashes (—): Count every em dash in the article. Em dash count > 0 = FAIL.
2. FORMATTING — Bold overuse: Flag if more than 3 bolded phrases per 500 words.
3. FORMATTING — Bullet-heavy sections: Flag if any section is 90%+ bullet points with no prose.
4. FORMATTING — Inline-header bullet lists: Flag any bullet that begins with a bolded phrase acting as a mini-header.
5. SENTENCE STRUCTURE — Hedging: Flag excessive qualifier stacking ("somewhat", "perhaps", "it could be argued", "it is worth noting", "it is important to note").
6. SENTENCE STRUCTURE — Hollow intensifiers: Flag "very", "truly", "really", "incredibly", "absolutely", "deeply" used as filler before adjectives.
7. SENTENCE STRUCTURE — Rule of three overuse: Flag if 3+ instances of X, Y, and Z list structure appear in the same article.
8. SENTENCE STRUCTURE — Copula avoidance: Flag if most sentences use "be" verbs (is/are/was/were/has been) instead of active verbs. Active verbs reveal specificity; copulas reveal vagueness.
9. SENTENCE STRUCTURE — Superficial -ing analyses: Flag opening clauses like "Standing at the edge of...", "Looking at...", "Considering the...", "Navigating the complex...".
10. WORD REPLACEMENTS — Flag any of these exact words/phrases and note their location:
    leverage, utilize, facilitate, implement, demonstrate, endeavor, commence, prioritize,
    comprehensive, robust, innovative, cutting-edge, seamless, streamline, synergy,
    delve, embark, pivotal, testament to, foster, in the realm of, at the end of the day,
    game-changer, look no further, it goes without saying, needless to say,
    in today's [X] landscape, in today's world, when it comes to, more than ever,
    a testament to, serves as a reminder, take it to the next level, stands out from the crowd.
11. TEMPLATE PHRASES — Flag: "In conclusion", "In summary", "To summarize", "As we've seen",
    "Whether you're a [X] or a [Y]", "No matter what", "At the end of the day",
    "The bottom line is", "All in all", "When all is said and done".
12. TRANSITION PHRASES — Flag: "Moreover", "Furthermore", "Additionally", "It's worth noting",
    "It is important to note", "That said", "Having said that", "With that in mind",
    "On the other hand", "In light of", "Given the above".
13. SIGNIFICANCE INFLATION — Flag language that makes ordinary things sound historic or transformative:
    "revolutionize", "unprecedented", "landmark", "paradigm shift", "groundbreaking",
    "once-in-a-generation", "redefine the way", "changed everything", "will never be the same".
14. SYNONYM CYCLING — Flag if the same concept is described with 3+ different synonyms in one article
    purely for variety (e.g. "purchase" → "acquire" → "obtain" → "procure").
15. VAGUE ATTRIBUTIONS — Flag: "experts say", "studies show", "research suggests", "according to experts",
    "many people believe", "it is widely known", "most agree" without naming a specific source.
16. FILLER PHRASES — Flag: "It's no secret that", "Let's face it", "The fact of the matter is",
    "The truth is", "Believe it or not", "At the heart of", "Needless to say".
17. GENERIC CONCLUSIONS — Flag conclusions that restate the intro, summarize without adding new information,
    or end with a generic call to action not tied to a specific product or action.
18. CHATBOT ARTIFACTS — Flag: "Certainly!", "Of course!", "Great question!", "As an AI",
    "I hope this helps", "Feel free to", "Don't hesitate to", meta-commentary about the article itself.
19. NOTABILITY NAME-DROPPING — Flag name-dropping of brands, institutions, or experts that adds no
    substantive information (e.g. "As seen on major platforms..." without specifics).
20. PROMOTIONAL LANGUAGE — Flag superlatives without evidence: "the best", "the most", "unmatched",
    "superior", "top-tier", "best-in-class" applied to alternatives without specific justification.
21. TITLE CASE HEADINGS — Flag any H2 or H3 that uses Title Case instead of Sentence case
    (e.g. "The Best Features Of This Product" should be "The best features of this product").

=== PASS CRITERIA (ALL must be true) ===

- human_voice >= 4
- warmth >= 4
- readability >= 3
- accuracy >= 3
- affiliate_link_present = true (an Amazon affiliate link -- amzn.to/... or amazon.com/dp/... -- is present)
- em_dash_count = 0 (any em dash = FAIL, no exceptions)
- NO first-person voice (I, we, us, our, my used as author voice = FAIL regardless of scores)
- If roundup: alternative product sections must have specific distinguishing details, not generic filler

Score 4 or 5 only if genuinely non-AI-sounding. When in doubt, score lower.

=== OUTPUT ===

Return ONLY a single valid JSON object. No preamble, no markdown fences, no trailing text.
Begin with {{ and end with }}.

{{
  "pass": true,
  "scores": {{"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}},
  "affiliate_link_present": true,
  "em_dash_count": 0,
  "ai_patterns_found": [],
  "flags": [],
  "rewrite_instructions": ""
}}

Rules:
- ai_patterns_found: list each AI pattern detected as "CATEGORY_NUMBER: description" (e.g. "10: uses leverage, seamless")
- flags: list each specific problem as a plain string; empty array if none
- rewrite_instructions: name exact sections and specific fixes if pass=false; empty string if pass=true. Keep under 250 words -- a truncated response fails JSON parsing and the article is held unreviewed.
- em_dash_count: exact integer count of (—) characters in article
- pass=false if em_dash_count > 0, first-person voice present, or human_voice < 4 or warmth < 4
"""


def make_rewrite_prompt(title: str, keyword: str, content: str, instructions: str, affiliate_url: str = "", product_name: str = "") -> str:
    affiliate_reminder = ""
    if affiliate_url and affiliate_url not in content:
        affiliate_reminder = (
            f"\nCRITICAL: The article is missing its affiliate link. "
            f"You MUST include this exact markdown link at least once -- in the opening mention of the product and again in the closing call-to-action: "
            f"[{product_name or 'this product'}]({affiliate_url})\n"
        )
    return f"""You are a senior writer for Happy Pet Product Reviews. Rewrite fixing ONLY the flagged issues.

ARTICLE TITLE: {title}
FOCUS KEYWORD: {keyword}
EDITOR FEEDBACK: {instructions}{affiliate_reminder}

ORIGINAL ARTICLE:
---
{content}
---

REWRITE RULES:
- Fix exactly what the editor flagged. Do not rewrite sections that passed.
- These hard rules from the original brief still apply -- breaking any one fails the article again:
  - NEVER use em dashes (—). Use hyphens, commas, or shorter sentences.
  - NEVER use first-person voice (I, we, us, our, my). No personal stories and no named pets. Write in second or third person.
  - NEVER invent numbers -- no percentages, review counts, prices, dates, or specs you were not given. If a number is not already in the article, do not add one.
- Where the editor flagged generic or AI-patterned writing, replace with something SPECIFIC and concrete.
  A specific detail beats a fluent generality every time.
  BAD: "Many cat owners find this litter box easy to clean."
  GOOD: "The front-entry design means you scoop from the top instead of kneeling on the floor."
- Where warmth or human voice is flagged, get MORE SPECIFIC in second person -- name a scenario the reader recognizes ("your dog paces the kitchen at 5am") or a concrete frustration. Do NOT invent a personal anecdote, a named pet, a testimonial, or a statistic to sound warmer. Real warmth is specific and grounded, never fabricated.
- Where transitions feel templated, cut them or rewrite as a direct statement.
  Never use: "Overall", "In summary", "Whether you", "At the end of the day", "Ultimately", "However", "Furthermore", "Moreover".
- Read your output before returning it. If any sentence could have been written by a content farm, rewrite it. If you added an em dash, a first-person word, or a number that was not in the original, remove it.

Return ONLY clean Markdown. No YAML. No preamble. First line must be:
PIN_DESC: [one punchy sentence, max 20 words, Pinterest stop-scroll hook]
Then article body immediately after."""


def make_prompt(title: str, keyword: str, slug: str, fmt: str, product: dict,
                related_url: str, related_anchor: str) -> str:
    affiliate_url = product.get("affiliate_url", "")
    product_name  = product.get("name", "")
    stars         = product.get("stars", "")
    review_count  = product.get("review_count", "")
    price         = product.get("price", "")
    link = ""
    if related_url and related_anchor:
        link = f"\nNaturally include this markdown link once where relevant: [{related_anchor}]({related_url})"
    affiliate_block = ""
    if product_name and affiliate_url:
        affiliate_block = (
            f"FEATURED PRODUCT: {product_name}\n"
            f"AFFILIATE LINK: {affiliate_url}\n"
            f"LINKING RULE:\n"
            f"- Always link {product_name} on its FIRST mention in the article.\n"
            f"- Always link {product_name} in the CLOSING call-to-action.\n"
            f"- For all mentions in between, link no more than 3 additional times.\n"
            f"- MAXIMUM 5 affiliate links total per article. Do not exceed this.\n"
            f"- All other mentions of {product_name} must be plain text, no link.\n"
            f"- LINK FORMAT: Always use the product name as anchor text: [{product_name}]({affiliate_url}). NEVER display the raw URL as text or as anchor text. NEVER write [{affiliate_url}]({affiliate_url}).\n"
        )
    if fmt == "single_review":
        structure = f"""ARTICLE FORMAT: In-depth single product review of {product_name}
STRUCTURE: Opening (100+ words) | Product Overview (H2) | What We Like (H2, 4-5 features) | What Could Be Better (H2, 2-3 honest drawbacks) | Real Owner Experiences (H2) | Who Should Buy This (H2) | Verdict (H2, 80+ words with affiliate link) | Star rating: **Our Rating: X/5**"""
    elif fmt == "roundup":
        # Build verified data block — only include fields we actually have
        verified_data = ""
        if stars:
            verified_data += f"  - Star rating : {stars}/5 (verified from Amazon)\n"
        if review_count:
            verified_data += f"  - Review count: {int(review_count):,} Amazon reviews\n"
        if price:
            verified_data += f"  - Price        : ${price} (verified from Amazon)\n"
        verified_block = ""
        if verified_data:
            verified_block = (
                f"VERIFIED PRODUCT DATA (use exactly as shown, do not alter or invent):\n"
                f"{verified_data}"
            )
        structure = f"""ARTICLE FORMAT: Roundup/comparison -- {title}

{verified_block}
STRUCTURE:
  Opening (100+ words, NO heading - begin prose directly)
  Quick Picks (H2)

  Featured Pick: {product_name} (H3, 150-200 words)
    - Reference the verified star rating and review count naturally in prose if available
    - Pros bullet list: 3-4 genuine strengths
    - Cons bullet list: 1-2 honest limitations
    - Include affiliate link per LINKING RULE above
    - Do not fabricate specs; hedge unverified claims ("many owners report..." / "tends to...")
    - NO invented personal stories, named dogs, specific dates, or fabricated test metrics

  Additional Picks: Use ONLY these real products from web search (H3 each, 60-75 words)
    {{ALTERNATIVE_PRODUCTS}}
    - Write each as a single prose paragraph, NOT a bullet list
    - Naturally include 1-2 genuine strengths AND 1-2 honest limitations
    - DO NOT include star ratings, prices, specific specs, or fabricated statistics/percentages you cannot verify -- omit numbers entirely
    - Hedge unverified claims: "tends to...", "most owners find...", "works well for..."
    - DO NOT fabricate review data like "88% of owners reported..." -- if you don't have the number, don't include one
    - Use ONLY the products listed above; do not add or invent others
    - NO links for additional picks unless a URL is explicitly provided in the prompt

  Buying Guide (H2, 150+ words)

  Comparison Table (H2): Product | Best For | Price Range | Key Attribute
    - Price Range: use $, $$, $$$ only; do not invent specific dollar amounts for additional picks
    - Key Attribute: choose the most relevant column header for this product category (e.g. Form, CFU Count, Flavor, Size). Never use "Chew Time" for non-consumable products.
    - Do NOT include a ratings column; only use verified ratings from product data above

  Closing (80+ words with affiliate link per LINKING RULE above, NO heading - begin prose directly)"""
    else:
        structure = f"""ARTICLE FORMAT: Buying guide -- {title}
STRUCTURE: Opening (100+ words, NO heading - begin prose directly) | What to Look For (H2, 5-6 key factors) | Our Top Pick {product_name} (H2, 100 words, affiliate link) | Common Mistakes to Avoid (H2, 3-4 pitfalls) | FAQ (H2, 4-5 real questions) | Closing (80+ words with affiliate link, NO heading - begin prose directly)"""
    return f"""You are a senior writer for Happy Pet Product Reviews, a trusted budget-focused pet product review blog.

Write a complete, publish-ready blog post. Title: "{title}". Focus keyword: "{keyword}".

{affiliate_block}
LENGTH: 950-1100 words of body content. Firm requirement. CRITICAL: Complete ALL sections before stopping. Do not stop early. Write every section in STRUCTURE completely.

{structure}

WRITING STYLE & HUMANIZATION RULES:
- Tone: conversational, grounded, and slightly skeptical. Avoid the hyper-enthusiastic, overly polished "salesperson" voice common in AI copy. Write like a real, budget-conscious dog or cat owner giving a friend honest, practical advice.
- Cadence: vary sentence length. Mix short, punchy sentences with a few longer flowing ones for a natural human rhythm. Do not make every sentence the same length; keep it readable and do not overuse one-word fragments.
- Transitions: do not use robotic transitions ("Furthermore", "Moreover", "Additionally", "Consequently", "Therefore", "However", "That said"). Start sentences with the subject or an action; an occasional plain "But", "And", or "So" is fine, but do not lean on them.
- No decorative bold: do not bold words or phrases inside prose paragraphs for emphasis. Keep prose styling plain.
- Use hyphens (-) for compound words and standard dashes where needed. NEVER use em dashes (—). Rewrite the sentence instead.
- Avoid rule-of-three overuse: do not stack three-item parallel phrases ("X, Y, and Z"). Use one or two items or restructure. More than one such list per article reads as AI-written.
- BANNED words and phrases, never use: "delve", "tapestry", "testament", "paramount", "crucial", "elevate", "beacon", "multifaceted", "seamlessly", "realm", "bustling", "unleash", "prioritize", "leverage", "robust", "the bottom line is", "it's worth noting", "in conclusion", "look no further", "game-changer", "comprehensive guide", "navigate", "we've all been there", "there's nothing quite like", "we've got you covered", "for good reason", "without breaking the bank", "in today's world", "when it comes to", "at the end of the day", "we all know", "as pet owners", "as dog owners", "as cat owners".
- Avoid stock pet-blog phrases that signal AI copy: never use "paw-some", "put our paws", "tail wagging" as metaphor, "furry family member", "fur baby", "pet parent", or "furry friend". Use "dog owner" or "cat owner" instead. Natural warmth through genuine voice is encouraged; forced wordplay is not.
- FACTS: only state product specs you are certain of from the product listing. When you lack a number, be concretely specific ("owners of large breeds", "on hardwood floors") rather than repeating a vague "many owners report...". Never invent dimensions, materials, weight, compatibility claims, percentages, statistics, prices, or review counts you were not given.
- SECTION HEADINGS: never start a section with "In conclusion" or "In summary". Use a specific, descriptive heading in sentence case (not Title Case). Never use "Opening" or "Closing" as headings; those are unheaded prose sections.
- OPENING: when it fits the topic, open with a specific, relatable moment a dog or cat owner would recognize, written in SECOND person. Show, don't tell.
  Good examples: "You step away for a 45-minute Zoom call and come back to a shredded couch cushion." / "Your cat knocks the water bowl over three times in one afternoon." / "You spend $40 on a toy your dog sniffs once and abandons."
  Bad examples (NEVER write openings like these): "We've all been there..." (cliche) / "As a pet owner, you know how important it is to..." (filler) / "Dogs need mental stimulation to stay happy and healthy." (generic) / "Standing in the kitchen when suddenly..." (AI-template setup) / any opening that starts with a vague scenario followed by a product pitch.
  If the article topic is purely practical (e.g. flea prevention, nutrition), a direct factual opening is fine; do not force an anecdote.
- Use "{keyword}" naturally 4-6 times. Write ONLY in second person ("your dog", "you'll find") or third person ("owners report", "dogs tend to"). Never use first-person voice (I, we, us, our, my); the reviewer fails any article that does.{link}

FORMAT: Return ONLY clean Markdown. No YAML. No preamble. Start writing immediately.
FIRST LINE must be: PIN_DESC: [one punchy sentence, max 20 words, Pinterest stop-scroll hook]
Then article body immediately after."""


def _sanitize_factcheck_output(cleaned: str, original: str) -> str | None:
    """
    Validate/clean a fact-checker response before it replaces the article.
    Returns the sanitized article, or None if the original must be kept.
    Chat models routinely wrap output in code fences or prepend 'Here is the
    corrected article:' -- and a response that dropped the affiliate links
    would previously have shipped as long as it cleared the length floor.
    """
    text = cleaned.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    first_nl = text.find("\n")
    if first_nl != -1:
        first_line = text[:first_nl].strip().lower()
        if first_line.endswith(":") and (
            "article" in first_line or first_line.startswith(("here", "sure", "below", "okay"))
        ):
            text = text[first_nl + 1:].lstrip()
    if len(text) < len(original) * 0.85:
        log(f"  Fact-check output too short ({len(text)} vs {len(original)}), keeping original", "WARN")
        return None
    # Protect all recognized affiliate-link shapes (amzn.to and amazon.com/dp),
    # not just short links -- the fact-checker must not drop or alter them.
    if sorted(AFFILIATE_LINK_RE.findall(text)) != sorted(AFFILIATE_LINK_RE.findall(original)):
        log("  Fact-check output altered affiliate links, keeping original", "WARN")
        return None
    return text


def fact_check_alternatives(content: str, primary_product: str) -> str:
    """Strip unverifiable stats from alternative product sections. Runs only on roundups."""
    content_fc = content  # full article always passed to fact-checker
    prompt = f"""You are a fact-checker for a pet product review blog. The article below has a FEATURED product ({primary_product}) with verified data, and ALTERNATIVE products with potentially fabricated statistics.

TASK: Review the ALTERNATIVE product sections (not the featured product) for two types of problems:

PART 1 -- Specific numbers: Find ALL specific numbers in alternative sections. This includes:
- Star ratings (e.g. "4.5-star rating")
- Review counts (e.g. "over 12,000 reviews")
- Percentages (e.g. "85% of reviewers", "reduce by up to 80%")
- Study counts (e.g. "over 20 clinical studies")
- Any other specific numerical claim

For EACH number found, replace it with hedged language:
- "4.5-star rating on Amazon" -> "strong ratings on Amazon"
- "85% of Amazon reviewers" -> "most Amazon reviewers"
- "over 12,000 Amazon reviews" -> "thousands of Amazon reviews"
- "reduce bad breath by up to 80%" -> "shown to significantly reduce bad breath"
- "over 20 clinical studies" -> "multiple clinical studies"

PART 2 -- Ingredient and mechanism claims: Check each specific ingredient or clinical mechanism named for an alternative product. Apply this rule:
- If you are CONFIDENT the ingredient is correct for that exact product (e.g. delmopinol in OraVet), keep it.
- If you are NOT CONFIDENT the ingredient is correct for that exact product, replace the specific claim with general language.
  Examples:
  - "contains chlorhexidine" (if uncertain) -> "uses an active antimicrobial system"
  - "formulated with zinc gluconate" (if uncertain) -> "formulated with active ingredients to support oral health"
  - When uncertain, describe the FUNCTION, not the specific ingredient.
- Never leave a specific ingredient claim that you cannot verify. General is always safer than wrong.

DO NOT change anything in the Featured Pick section.
DO NOT change any other content, structure, headings, links, or prose.
Return the COMPLETE article with only the flagged claims replaced.

ARTICLE:
{content_fc}"""

    # --- Primary: Gemini Flash Lite (paid tier -- reliable, non-reasoning,
    # so the full-article echo fits comfortably in the output budget) ---
    try:
        cleaned = _call_gemini(FACTCHECK_MODEL, prompt, max_tokens=8192,
                               temperature=0.1, label="FactCheck-Gemini", timeout=90)
        sanitized = _sanitize_factcheck_output(cleaned, content)
        if sanitized is None:
            return content
        log(f"  Fact-check ok: {len(content)} -> {len(sanitized)} chars")
        return sanitized
    except Exception as exc:
        log(f"  Fact-check primary failed: {exc} -- trying fallback", "WARN")

    # --- Fallback: OpenRouter gpt-oss-20b:free ---
    try:
        or_fc_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not or_fc_key:
            raise ValueError("OPENROUTER_API_KEY not set -- cannot run fact-check fallback")
        payload = json.dumps({
            "model": OR_FACTCHECK_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            # The task echoes the COMPLETE article; on a reasoning model the
            # reasoning tokens count against max_tokens, so keep headroom and
            # cap the reasoning effort.
            "max_tokens": 8192,
            "reasoning": {"effort": "low"},
            "temperature": 0.1,
        }).encode()
        or_fc_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {or_fc_key}",
            **OR_HEADERS_EXTRA,
        }
        raw     = http_post(OPENROUTER_URL, payload, or_fc_headers, label="FactCheck-OR",
                            timeout=60, retries=2, backoff_base=60)
        cleaned = _extract_or_content(raw, "FactCheck-OR")
        sanitized = _sanitize_factcheck_output(cleaned, content)
        if sanitized is None:
            return content
        log(f"  Fact-check fallback ok ({OR_FACTCHECK_MODEL}): {len(content)} -> {len(sanitized)} chars")
        return sanitized
    except Exception as exc:
        log(f"  Fact-check fallback failed: {exc} -- keeping original", "WARN")
        return content


def find_alternative_products(keyword: str, primary_product: str, groq_key: str, count: int = 3) -> str:
    """Find real alternative products via OpenRouter (Groq removed -- CF-blocked from GHA)."""
    prompt = (
        f"Name the top {count} popular alternatives to {primary_product} for '{keyword}'. "
        f"For each, provide: brand name, product name, and one sentence that includes a SPECIFIC "
        f"differentiating feature (e.g. a key ingredient, a unique design element, or a specific "
        f"use case it excels at). Be concrete, not vague. "
        f"Return as a simple numbered list: Brand - Product Name: Description"
    )

    or_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not or_key:
        log("  OPENROUTER_API_KEY not set -- skipping alternative search", "WARN")
        return ""

    or_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {or_key}",
        **OR_HEADERS_EXTRA,
    }

    # Tier 1: gpt-oss-120b:free (Groq removed -- CF error 1010 blocks all Groq from GHA)
    payload = json.dumps({
        "model": OR_GEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.3,
    }).encode()

    try:
        raw     = http_post(OPENROUTER_URL, payload, or_headers, label="AltSearch-OR-120b", timeout=60, retries=1)
        content = json.loads(raw)["choices"][0]["message"]["content"]
        log(f"  Found {count} alternatives via OR gpt-oss-120b:free")
        return content
    except Exception as exc:
        log(f"  OR-120b alt search failed: {exc} -- trying OR-20b fallback", "WARN")

    # Tier 2: gpt-oss-20b:free
    payload = json.dumps({
        "model": "openai/gpt-oss-20b:free",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.3,
    }).encode()

    try:
        raw     = http_post(OPENROUTER_URL, payload, or_headers, label="AltSearch-OR-20b", timeout=60, retries=1)
        content = json.loads(raw)["choices"][0]["message"]["content"]
        log(f"  Found {count} alternatives via OR gpt-oss-20b:free fallback")
        return content
    except Exception as exc:
        log(f"  Alternative search failed on both OR models: {exc}", "WARN")
        return ""


def review_and_rewrite(title: str, keyword: str, content: str, api_key: str, or_key: str = "", affiliate_url: str = "", product_name: str = "") -> tuple:
    """Returns (final_content, passed, flags)"""
    if not REVIEWER_ENABLED:
        return content, True, []
    for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
        log_reviewer(f"  REVIEW attempt {attempt}/{MAX_REVIEW_ATTEMPTS}")
        log_reviewer(f"  REVIEW pre-sleep {REVIEW_PRE_SLEEP}s...")
        time.sleep(REVIEW_PRE_SLEEP)
        review_prompt = make_review_prompt(title, keyword, content)
        scorecard = None

        # --- Primary: Claude Haiku 4.5 via OpenRouter, schema-enforced JSON ---
        # Cross-family judge (Gemini writes, Claude reviews) + server-side schema
        # validation: the old regex-repair passes and REVIEWER_JSON_ERROR class
        # existed because free models returned malformed scorecards.
        try:
            scorecard = _call_openrouter_reviewer(review_prompt)
            log_reviewer(f"  Reviewer: {REVIEWER_MODEL} (via OpenRouter, schema-enforced)")
        except Exception as _claude_exc:
            log_reviewer(f"  Haiku reviewer error: {_claude_exc} -- falling back to Gemini", "WARN")

        # --- Fallback: Gemini Flash Lite with responseSchema ---
        if scorecard is None:
            try:
                raw = _call_gemini(REVIEWER_FALLBACK, review_prompt,
                                   max_tokens=4000, temperature=0.1,
                                   label="Reviewer-Gemini-Fallback", log_fn=log_reviewer,
                                   json_schema=REVIEW_SCHEMA_GEMINI, timeout=60)
                scorecard = json.loads(raw)
                log_reviewer(f"  Reviewer fallback: {REVIEWER_FALLBACK} (Gemini, responseSchema)")
            except Exception as _gem_exc:
                log_reviewer(f"  Gemini reviewer error: {_gem_exc}", "WARN")

        if scorecard is None:
            log_reviewer("  review failed on both providers -- article held as UNREVIEWED", "WARN")
            return content, False, ["REVIEWER_UNAVAILABLE"]
        scores      = scorecard.get("scores", {})
        em_dashes   = scorecard.get("em_dash_count", 0)
        ai_patterns = scorecard.get("ai_patterns_found", [])
        # Decide pass/fail in code -- never trust the reviewer's self-reported
        # `pass`, numeric scores, or em-dash count on their own (evaluate_scorecard).
        model_pass    = scorecard.get("pass", False)
        passed, flags = evaluate_scorecard(scorecard, content)
        log_reviewer(f"  REVIEW {'PASS' if passed else 'FAIL'} | human_voice={scores.get('human_voice')} "
            f"warmth={scores.get('warmth')} readability={scores.get('readability')} em_dashes={em_dashes}")
        if passed != model_pass:
            log_reviewer(f"  OVERRIDE: model pass={model_pass} -> {passed} (deterministic gate)", "WARN")
        if ai_patterns:
            log_reviewer(f"  AI PATTERNS: {'; '.join(ai_patterns[:5])}")
        if flags:
            log_reviewer(f"  FLAGS: {'; '.join(str(f) for f in flags)}")
        if passed:
            return content, True, []
        instructions = scorecard.get("rewrite_instructions", "")
        if not instructions and (flags or scorecard.get("ai_patterns_found")):
            # Terse/truncated reviewers often fail an article but return empty
            # rewrite_instructions -- which used to abort the 3-attempt loop
            # after one try. Synthesize instructions from what it did report.
            problems = list(flags) + list(scorecard.get("ai_patterns_found") or [])
            instructions = "Fix each of these reviewer findings: " + "; ".join(str(p) for p in problems[:12])
            log_reviewer("  rewrite_instructions empty -- synthesized from flags/patterns", "WARN")
        if attempt < MAX_REVIEW_ATTEMPTS and instructions:
            # Attempt 1 rewrite: use original model (Gemini)
            # Attempt 2 rewrite: use failover model (Groq Llama 70B)
            if attempt == 1:
                log_reviewer("  REWRITING via Gemini (original model)...")
                time.sleep(RPM_SLEEP)
                try:
                    content = call_generator(make_rewrite_prompt(title, keyword, content, instructions, affiliate_url=affiliate_url, product_name=product_name), api_key)
                    _, content = extract_pin_desc(content, "")
                except Exception as e:
                    log_reviewer(f"  WARN: Gemini rewrite failed: {e}")
                    return content, False, flags
            else:
                log_reviewer(f"  REWRITING via {GEMINI_REWRITE_MODEL} (attempt 2 failover)...")
                time.sleep(RPM_SLEEP)
                rw_prompt_text = make_rewrite_prompt(title, keyword, content, instructions, affiliate_url=affiliate_url, product_name=product_name)
                rw_content = None
                # Tier 1: Gemini Flash Lite (different model than attempt 1's generator)
                try:
                    rw_content = _call_gemini(GEMINI_REWRITE_MODEL, rw_prompt_text,
                                              max_tokens=8192, temperature=0.7,
                                              label="Rewriter-Gemini", log_fn=log_reviewer)
                except Exception as e:
                    log_reviewer(f"  Gemini rewrite failed: {e} -- trying OR fallback", "WARN")
                # Tier 2: OpenRouter gpt-oss-120b:free
                if not rw_content:
                    try:
                        or_fb_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
                        rw_or_payload = json.dumps({
                            "model": OR_GEN_MODEL,
                            "messages": [{"role": "user", "content": rw_prompt_text}],
                            "max_tokens": 8192, "temperature": 0.7,
                        }).encode()
                        rw_or_headers = {"Content-Type": "application/json",
                                         "Authorization": f"Bearer {or_fb_key}",
                                         **OR_HEADERS_EXTRA}
                        raw_rw     = http_post(OPENROUTER_URL, rw_or_payload, rw_or_headers,
                                               label="Rewriter-OR", log_fn=log_reviewer,
                                               timeout=90, retries=2, backoff_base=60)
                        rw_content = _extract_or_content(raw_rw, "Rewriter-OR")
                    except Exception as e:
                        log_reviewer(f"  WARN: OR rewrite failed: {e}")
                        return content, False, flags
                content = rw_content
                _, content = extract_pin_desc(content, "")
        else:
            log_reviewer(f"  REVIEW FAILED after {attempt} attempt(s) -- creating GitHub issue", "WARN")
            return content, False, flags
    return content, False, []


def create_github_issue(title: str, slug: str, flags: list) -> None:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GH_TOKEN": os.environ.get("GITHUB_TOKEN", os.environ.get("GH_TOKEN", ""))}
    flag_text = "\n".join(f"- {f}" for f in flags) if flags else "- Review failed quality threshold"
    body = (
        f"## Article Quality Review Failed\n\n"
        f"**Article:** {title}\n**Slug:** `{slug}`\n"
        f"**Date:** {datetime.date.today().isoformat()}\n\n"
        f"### Flags\n{flag_text}\n\n"
        f"### Action Required\n"
        f"1. Review `_posts/` file\n2. Edit manually or re-run generator\n3. Close once published\n"
    )
    cmd = ["gh", "issue", "create", "--repo", GITHUB_REPO,
           "--title", f"[Review Required] {title}", "--body", body, "--assignee", GITHUB_ASSIGNEE]
    try:
        r = subprocess.run(cmd, env=env, capture_output=True, text=True)
        log_reviewer(f"  GITHUB ISSUE: {r.stdout.strip() if r.returncode == 0 else r.stderr[:80]}")
    except FileNotFoundError:
        # gh missing must not crash the caller -- an uncaught error here was
        # counted as a generation FAIL instead of a held article
        log_reviewer("  GITHUB ISSUE skipped: gh CLI not available", "WARN")


def yaml_quote(text: str) -> str:
    """
    Make a string safe inside double-quoted YAML front matter.
    Title/description/pin_desc are LLM output -- a stray quote or newline
    would otherwise break the front matter and fail the whole Jekyll build.
    """
    return str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ").strip()


def front_matter(title: str, keyword: str, affiliate_url: str, slug: str,
                 species: str, category: str, description: str, image: str = "",
                 pin_image: str = "", chewy_url: str = "") -> str:
    today = datetime.date.today().isoformat()
    fm = (
        f'---\nlayout: post\ntitle: "{yaml_quote(title)}"\ndate: {today}\n'
        f'categories: [{category}]\nspecies: {species}\ntags: [{keyword}]\n'
        f'description: "{yaml_quote(description)}"\n'
    )
    if affiliate_url:
        fm += f'affiliate_url: "{affiliate_url}"\n'
    if chewy_url and not chewy_url.startswith("REVIEW"):
        fm += f'chewy_url: "{chewy_url}"\n'
    if image:
        fm += f'image: "{image}"\n'
    if pin_image:
        fm += f'pin_image: "{pin_image}"\n'
    fm += '---\n'
    return fm


def persist_generated_article(draft_path, article_text: str, pin_queue_path, pin_data: dict) -> None:
    """Stage the pin-queue entry, THEN write the draft.

    The draft file is the last thing written on the success path. If anything
    here fails (serialization, disk, a mid-write crash), no draft exists -- so
    Stage 2 can never publish a draft that has no pin queued for it (an orphan).
    """
    pin_queue_path.parent.mkdir(exist_ok=True)
    pin_queue_path.write_text(json.dumps(pin_data, indent=2), encoding="utf-8")
    draft_path.write_text(article_text, encoding="utf-8")


def main() -> None:
    # Load .env first -- local runs need this; GHA already has env vars from secrets
    if DOTENV_AVAILABLE:
        load_dotenv(Path.home() / ".env")

    # Reduce inter-article delay on multi-article runs to stay within Stage 1 timeout
    global INTER_DELAY
    # Single authoritative read -- this used to be parsed twice with different
    # defaults ("1" here, "999" at the cap), so an unset MAX_ARTICLES queued
    # every topic while keeping the slow single-article delay
    _max_raw = os.environ.get("MAX_ARTICLES", "").strip()
    max_articles = int(_max_raw) if _max_raw.isdigit() and int(_max_raw) > 0 else 1
    if max_articles > 1:
        INTER_DELAY = 120
        log("Multi-article run detected -- INTER_DELAY reduced to 120s")

    groq_key = os.environ.get("GROQ_API_KEY", "").strip()

    if LOCK_PATH.exists():
        old = LOCK_PATH.read_text().strip()
        try:
            os.kill(int(old), 0)
            log(f"Already running (PID {old}). Exiting.", "WARN"); return
        except (OSError, ValueError):
            log(f"Stale lock (PID {old}) -- clearing", "WARN"); LOCK_PATH.unlink()
    LOCK_PATH.write_text(str(os.getpid()))

    try:
        if not groq_key:
            log("GROQ_API_KEY not set -- not required (Groq removed)", "WARN")

        # Load products -- also registers categories into SLUG_CATEGORIES
        products = load_products()
        log(f"Loaded products.json: {len(products)} entries")

        # TOPICS entirely from products.json -- no hardcoded list
        topics = [
            (p["topic"], p["title"], p["keyword"], p["format"])
            for p in products.values()
            if all(k in p for k in ("topic", "title", "keyword", "format"))
        ]
        log(f"Topics from products.json: {len(topics)}")

        # Startup validation pass -- warn on missing fields before any API calls
        for slug, p in products.items():
            errors = validate_product(slug, p)
            if errors:
                log(f"  VALIDATION WARN [{slug}]: {'; '.join(errors)}", "WARN")

        used_slugs = build_used_slugs()
        log(f"Dedup: {len(used_slugs)} slugs already published")

        # Filter already-published slugs BEFORE applying cap so cap slots
        # are never wasted on topics that will be skipped anyway.
        topics = [t for t in topics if t[0] not in used_slugs]
        log(f"Unpublished topics available: {len(topics)}")

        topics = topics[:max_articles]
        log(f"Cap: {max_articles} -- {len(topics)} topic(s) queued this run")

        POSTS_DIR.mkdir(parents=True, exist_ok=True)
        today     = datetime.date.today().isoformat()
        generated = skipped = failed = held = 0
        log(f"START v22.0 -- {len(topics)} topics -- generator={OR_GEN_MODEL_CLAUDE} (fallback {GEMINI_GEN_MODEL}) reviewer={REVIEWER_MODEL if REVIEWER_ENABLED else 'OFF'}")

        for i, (slug, title, keyword, fmt) in enumerate(topics, 1):
            # Inter-article delay at the TOP of the loop so every path pays it.
            # When it lived at the bottom, every held/failed `continue` skipped
            # it and the next topic's API burst started immediately -- exactly
            # when the free-tier providers were already rate-limiting us.
            if i > 1:
                log(f"  Waiting {INTER_DELAY // 60}min before next topic...")
                time.sleep(INTER_DELAY)

            if slug in used_slugs:
                log(f"SKIP [{i}/{len(topics)}] {slug} -- already published"); skipped += 1; continue

            product = products.get(slug, {})

            # Pre-publish validation gate -- hold before spending any API tokens
            errors = validate_product(slug, product)
            if errors:
                log(f"HOLD [{i}/{len(topics)}] {slug} -- {'; '.join(errors)}")
                held += 1; continue

            category          = product.get("category", "pet-accessories")
            species           = product.get("species", "both")
            topical_sheet_key = product.get("topical_sheet", "")

            log(f"  Product: {product['name']}")
            enrich_with_chewy(slug, product)  # no-op if already set or creds missing
            log(f"WRITE [{i}/{len(topics)}] [{fmt}] {title}")
            _t0 = time.monotonic()
            time.sleep(RPM_SLEEP)

            # Dynamic internal link resolved from live _posts/
            related_url, related_anchor = find_related_published_slug(slug, category)

            try:
                # For roundup, use Apify runner-ups from products.json if available,
                # otherwise fall back to Groq Compound
                alternatives_text = ""
                if fmt == "roundup":
                    product_name = product.get("name", "")
                    runners_up = product.get("runners_up", "")
                    if runners_up:
                        alternatives_text = runners_up
                        log(f"  Alternatives: using Apify runner-ups from products.json")
                    else:
                        alternatives_text = find_alternative_products(keyword, product_name, groq_key, count=3)
                        log(f"  Alternatives: Groq fallback (no runners_up in products.json)")
                
                prompt  = make_prompt(title, keyword, slug, fmt, product, related_url, related_anchor)
                
                # Inject alternatives into roundup prompt with explicit count constraint
                if alternatives_text:
                    # runners_up from products.json is ';'-delimited; the LLM
                    # fallback returns a newline-separated numbered list --
                    # counting ';' on the latter said "EXACTLY 1" while listing 3
                    if ";" in alternatives_text:
                        alt_count = len([a for a in alternatives_text.split(";") if a.strip()])
                    else:
                        alt_count = len([ln for ln in alternatives_text.splitlines() if ln.strip()])
                    alt_constraint = (
                        "EXACTLY " + str(alt_count) + " alternative product(s) listed below. "
                        "Use ONLY these " + str(alt_count) + " product(s). "
                        "Do NOT add, invent, or substitute any others.\n"
                        + alternatives_text
                    )
                    prompt = prompt.replace("{{ALTERNATIVE_PRODUCTS}}", alt_constraint)
                else:
                    prompt = prompt.replace("{{ALTERNATIVE_PRODUCTS}}", "EXACTLY 3 alternatives -- use well-known brands you are confident exist. Do not fabricate products.")
                
                content = call_generator(prompt, groq_key)
                log(f"  [timing] generate: {time.monotonic()-_t0:.1f}s")

                pin_desc, content = extract_pin_desc(content, f"{title} - expert reviews and buying guide.")
                log_pin(f"  PIN_DESC: {pin_desc[:60]}")
                pin_desc = clean_pin_desc(pin_desc, species)
                if len(content) < 2000:
                    log(f"  only {len(content)} chars -- may be truncated", "WARN")

                # Gate 1: generation output contract
                try:
                    validate_output("generate", content, slug)
                except GenerationStageError as e:
                    log(f"  HOLD {slug} -- generation contract failed: {e}", "WARN")
                    held += 1; continue

                # Fact-check: strip fabricated stats from alternative product sections (roundups only)
                if fmt == "roundup":
                    content = fact_check_alternatives(content, product.get("name", ""))
                    log(f"  [timing] fact-check: {time.monotonic()-_t0:.1f}s")

                time.sleep(RPM_SLEEP)
                or_key_reviewer = os.environ.get("OPENROUTER_API_KEY", "").strip()
                content, review_passed, review_flags = review_and_rewrite(title, keyword, content, groq_key, or_key=or_key_reviewer, affiliate_url=product.get("affiliate_url", ""), product_name=product.get("name", ""))
                log(f"  [timing] review: {time.monotonic()-_t0:.1f}s")
                if not review_passed:
                    create_github_issue(title, slug, review_flags)
                    log(f"  HOLD {slug} -- quality check failed, GitHub issue created")
                    held += 1; continue

                # Deterministic banned-phrase scrub on the body -- the prompt
                # asks for it and the reviewer spot-checks it, but published
                # posts prove neither is airtight
                content = scrub_banned_phrases(content, species)

                # Gate 2: post-review output contract
                try:
                    validate_output("review", content, slug, affiliate_url=product.get("affiliate_url", ""))
                except GenerationStageError as e:
                    log(f"  HOLD {slug} -- post-review contract failed: {e}", "WARN")
                    held += 1; continue

                fname = f"DRAFT-{slugify(slug)}.md"  # Stage 2 dates on publish
                fpath = POSTS_DIR / fname
                fm    = front_matter(title, keyword, product.get("affiliate_url", ""),
                                     slug, species, category, pin_desc,
                                     product.get("image", ""),
                                     build_pin_image_url_for_queue(slug),
                                     chewy_url=product.get("chewy_url") or "")
                # Strip leading horizontal rules model sometimes prepends
                content_clean = content.lstrip()
                while content_clean.startswith("---"):
                    content_clean = content_clean[3:].lstrip()

                article_url = build_url(slug, utm=True)
                asin        = product.get("asin", "")
                pin_url     = product.get("image", "")
                # Prefer canonical Amazon CDN format -- direct m.media-amazon.com URLs
                # are blocked by GHA runner IPs; images-na CDN is consistently accessible
                if asin:
                    pin_url = f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.LZZZZZZZ.jpg"
                if PIN_GEN_AVAILABLE:
                    try:
                        pin_url = make_pin_for_post(title, pin_desc, pin_url, category, slug, generated)
                        log_pin(f"  PIN: {pin_url}")
                    except Exception as pe:
                        log_pin(f"  pin generation failed: {pe}", "WARN")

                # Stage the pin entry, THEN write the draft (draft-last): a failure
                # here can never leave an orphan draft that Stage 2 would publish
                # with no pin queued. See persist_generated_article.
                pin_data = {
                    "title": title, "article_url": article_url, "description": pin_desc,
                    "image_url": build_pin_image_url_for_queue(slug), "species": species, "slug": slug,
                    "topical_sheet": topical_sheet_key,
                }
                persist_generated_article(
                    fpath, fm + "\n" + content_clean,
                    REPO_DIR / "_pin_queue" / f"{slug}.json", pin_data,
                )
                log(f"  SAVED {fname} + staged pin {slug}.json -- total: {time.monotonic()-_t0:.1f}s")

                generated += 1
                used_slugs.add(slug)

            except Exception as exc:
                log(f"  FAIL: {exc}", "ERROR"); failed += 1

        log(f"DONE -- {generated} written, {skipped} skipped, {held} held, {failed} failed")

        # P4: Write machine-readable result for GHA exit gate.
        # GHA step reads this and exits non-zero if articles_generated and articles_held are both 0.
        result_path = REPO_DIR / "GENERATION_RESULT.json"
        result_path.write_text(json.dumps({
            "articles_generated": generated,
            "articles_held":      held,
            "articles_skipped":   skipped,
            "articles_failed":    failed,
        }, indent=2))
        log(f"RESULT: wrote GENERATION_RESULT.json (generated={generated} held={held} skipped={skipped} failed={failed})")

        # P7: Write PENDING_DRAFTS.json atomically so Stage 2 has a reliable signal.
        # Stage 2 reads this file; file present = drafts exist, file absent = nothing to publish.
        # This replaces fragile SHA polling with a deterministic file-based handoff.
        if generated > 0:
            draft_slugs = [
                md.stem.split("-", 1)[1]
                for md in sorted(POSTS_DIR.glob("DRAFT-*.md"))
            ]
            pending_path = REPO_DIR / "PENDING_DRAFTS.json"
            pending_path.write_text(json.dumps({
                "drafts":    draft_slugs,
                "generated": generated,
                "date":      datetime.date.today().isoformat(),
            }, indent=2))
            log(f"P7: wrote PENDING_DRAFTS.json with {len(draft_slugs)} slug(s): {draft_slugs}")

    finally:
        if LOCK_PATH.exists(): LOCK_PATH.unlink()


if __name__ == "__main__":
    main()