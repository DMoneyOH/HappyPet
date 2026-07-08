# Chewy GTIN Fast-Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the Amazon UPC of a product is known, use it to find an exact-match Chewy catalog item by GTIN and auto-accept it, bypassing the existing brand-conflict and word-coverage heuristics entirely — those heuristics exist only to *guess* whether two product names describe the same item, and a verified UPC match makes that guess unnecessary.

**Architecture:** `chewy_lookup.py` gets a new optional `upc` parameter threaded through `lookup()` → `find_best_match()`. Each keyword-variant search already returns a list of Impact.com catalog items (each carrying a `Gtin` field, confirmed live via direct API sampling on 2026-07-08 — 100% populated across 25 sampled items, spanning 5 different product queries). Before scoring those items by name-overlap, check each candidate's `Gtin` (normalized for leading-zero/width differences) against the known UPC. A hit short-circuits straight to auto-accept with a `gtin_matched` flag that skips the brand/coverage gates in `lookup()`. No hit falls through to the existing scoring path unchanged — this is strictly additive, not a replacement.

UPC capture only exists on the manual-resolution path for now (`manual_resolve.py --upc`, filled in by a human reading the "Product information" section of the Amazon page during the already-established live-browser resolution process). The automated `resolve_product()` Amazon-scrape path does not expose UPC and is out of scope — extending it would require either parsing Amazon's product page (not the search-results page it currently scrapes) or PA-API's `ItemInfo.ExternalIds` resource, and PA-API is explicitly parked per Derek's decision (see `HANDOFF.md`). This plan only wires the plumbing; it does not go back and backfill UPCs for the 19 already-REVIEW-flagged products in `products.json` — that's a separate, manual follow-up once this lands.

**Tech Stack:** Python 3, stdlib only (`re`, no new dependencies). Tests via `unittest`, existing `unittest.mock.patch.object` conventions already used throughout `test_pipeline.py`.

---

## Investigation findings (already done, informing this plan)

Queried the Impact.com `/Catalogs/{CatalogId}/Items` endpoint directly (`chewy_lookup.search_catalog`) for 5 unrelated product searches (ChomChom, Potaroma, Feliway, Blue Buffalo, Petbobi) and inspected raw item payloads:

- **`Gtin` is populated on every sampled item** (25/25) — e.g. `"Gtin": "700603718714"`.
- **`Asin` is always empty** (`""`) — Chewy does not cross-reference Amazon ASINs in this feed, so ASIN-based matching is not viable; GTIN/UPC is the only usable exact-match key.
- GTIN widths vary in practice (12-digit UPC-A vs 13/14-digit EAN/GTIN with leading zero padding) — matching must normalize for this, not compare raw strings.

## File Structure

- **Modify `chewy_lookup.py`**: add `_normalize_gtin()` (pure helper, next to `_word_coverage`), add `_find_gtin_match()` (next to `best_match()`), extend `find_best_match()` to accept `upc` and return a 3-tuple `(item, score, gtin_matched)`, extend `lookup()` to accept `upc` and skip the brand/coverage gates when `gtin_matched` is true.
- **Modify `refill_products.py`**: `chewy_enrich()` accepts and forwards `upc`; `apply_resolution()` reads `resolved.get("upc")`, stores it on the entry, and passes it to `chewy_enrich()`.
- **Modify `manual_resolve.py`**: add optional `--upc` CLI flag, included in the `resolved` dict only when provided.
- **Modify `test_pipeline.py`**: new `TestChewyGtinMatch` class (after `TestChewyWordCoverage`) covering the helpers and the `lookup()` fast path; two new tests in `TestManualResolve` for the `--upc` flag; one new test for `apply_resolution` → `chewy_enrich` wiring.
- **`products.json` schema**: gains an optional `"upc"` string field on entries where known. No migration needed — absence is the default for all 23 existing entries.

---

### Task 1: `_normalize_gtin` helper

**Files:**
- Modify: `chewy_lookup.py:150` (insert after `_word_coverage`, before the `# --- Impact.com API ---` section comment at line 153)
- Test: `test_pipeline.py` (insert new `TestChewyGtinMatch` class after `TestChewyWordCoverage`, which ends at line 459 — insert before `class TestBrainSecretsVaultFallback(unittest.TestCase):` at line 461)

- [ ] **Step 1: Write the failing tests**

Insert this new class into `test_pipeline.py` right before `class TestBrainSecretsVaultFallback(unittest.TestCase):` (line 461):

```python
class TestChewyGtinMatch(unittest.TestCase):
    """An exact UPC match against Chewy's catalog Gtin field is definitive --
    it should bypass the brand-conflict and word-coverage heuristics entirely,
    since those exist only to guess whether two *names* describe the same
    product. A verified UPC makes that guess unnecessary."""

    def test_normalize_gtin_strips_punctuation_and_leading_zeros(self):
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin("700603718714"), "700603718714")
        self.assertEqual(_normalize_gtin("00700603718714"), "700603718714")
        self.assertEqual(_normalize_gtin("0070-0603-718714"), "700603718714")

    def test_normalize_gtin_handles_gtin14_vs_upc12_padding(self):
        # GTIN-14 is UPC-A left-padded with zeros to 14 digits -- the two
        # must normalize to the same value or a real match gets missed.
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin("00000700603718714"[-14:]),
                         _normalize_gtin("700603718714"))

    def test_normalize_gtin_empty_input_returns_empty(self):
        from chewy_lookup import _normalize_gtin
        self.assertEqual(_normalize_gtin(""), "")
        self.assertEqual(_normalize_gtin(None), "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestChewyGtinMatch -v`
Expected: FAIL with `ImportError: cannot import name '_normalize_gtin'`

- [ ] **Step 3: Implement `_normalize_gtin`**

Insert into `chewy_lookup.py` immediately after the `_word_coverage` function (after the line `return len(a & b) / len(a | b)`, before the `# --- Impact.com API ---` section):

```python
def _normalize_gtin(code: str | None) -> str:
    """Digits-only, leading-zeros stripped. GTIN-14, EAN-13, and UPC-A are
    the same code at different check-digit widths -- stripping padding lets
    an exact-UPC match compare cleanly against whatever width Chewy's
    catalog happens to report. Empty/None input returns "" (never matches)."""
    digits = re.sub(r"[^0-9]", "", code or "")
    return digits.lstrip("0")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestChewyGtinMatch -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add chewy_lookup.py test_pipeline.py
git commit -m "feat(chewy): add _normalize_gtin helper for UPC/GTIN comparison"
```

---

### Task 2: `_find_gtin_match` helper

**Files:**
- Modify: `chewy_lookup.py:254` (insert immediately before `def find_best_match`, i.e. after `best_match()` ends)
- Test: `test_pipeline.py` (add methods to `TestChewyGtinMatch`)

- [ ] **Step 1: Write the failing tests**

Add these methods to the `TestChewyGtinMatch` class created in Task 1:

```python
    def test_find_gtin_match_returns_item_with_matching_gtin(self):
        from chewy_lookup import _find_gtin_match
        items = [
            {"Name": "Wrong Item", "Gtin": "111111111111", "StockAvailability": "InStock"},
            {"Name": "Right Item", "Gtin": "700603718714", "StockAvailability": "InStock"},
        ]
        match = _find_gtin_match(items, "700603718714")
        self.assertEqual(match["Name"], "Right Item")

    def test_find_gtin_match_normalizes_before_comparing(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Right Item", "Gtin": "00700603718714", "StockAvailability": "InStock"}]
        match = _find_gtin_match(items, "700603718714")
        self.assertEqual(match["Name"], "Right Item")

    def test_find_gtin_match_returns_none_when_no_upc_given(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Item", "Gtin": "700603718714", "StockAvailability": "InStock"}]
        self.assertIsNone(_find_gtin_match(items, ""))
        self.assertIsNone(_find_gtin_match(items, None))

    def test_find_gtin_match_returns_none_when_nothing_matches(self):
        from chewy_lookup import _find_gtin_match
        items = [{"Name": "Item", "Gtin": "999999999999", "StockAvailability": "InStock"}]
        self.assertIsNone(_find_gtin_match(items, "700603718714"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestChewyGtinMatch -v`
Expected: FAIL with `ImportError: cannot import name '_find_gtin_match'`

- [ ] **Step 3: Implement `_find_gtin_match`**

Insert into `chewy_lookup.py` immediately before `def find_best_match(product_name: str) -> tuple[dict | None, int]:`:

```python
def _find_gtin_match(items: list, upc: str | None) -> dict | None:
    """First filtered candidate (see _filter_candidates) whose Gtin exactly
    matches the known Amazon UPC, normalized for width/padding differences.
    Returns None if upc is falsy or no candidate's Gtin matches -- callers
    fall through to the ordinary name-scoring path in that case."""
    target = _normalize_gtin(upc)
    if not target:
        return None
    for item in _filter_candidates(items):
        if _normalize_gtin(item.get("Gtin", "")) == target:
            return item
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestChewyGtinMatch -v`
Expected: PASS (7 tests total in the class so far)

- [ ] **Step 5: Commit**

```bash
git add chewy_lookup.py test_pipeline.py
git commit -m "feat(chewy): add _find_gtin_match candidate lookup"
```

---

### Task 3: Wire the GTIN fast path into `find_best_match()` and `lookup()`

**Files:**
- Modify: `chewy_lookup.py:276-297` (`find_best_match`)
- Modify: `chewy_lookup.py:394-484` (`lookup`)
- Test: `test_pipeline.py` (add methods to `TestChewyGtinMatch`)

- [ ] **Step 1: Write the failing tests**

Add these methods to `TestChewyGtinMatch`:

```python
    def test_lookup_gtin_match_bypasses_low_coverage_downgrade(self):
        # Same regression pair as
        # test_lookup_downgrades_high_score_low_coverage_match_to_review, but
        # this time the searched product's known UPC exactly matches the
        # candidate's Gtin -- it must auto-accept despite low word coverage.
        import chewy_lookup as cl
        search_name = (
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped"
        )
        item = {
            "Name": "Blue Buffalo Blue Bits Tender Beef Dog Treats",
            "Manufacturer": "Blue Buffalo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/blue-bits-tender-beef",
            "CurrentPrice": "41.99",
            "Gtin": "840243160563",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=4.6):
            result = cl.lookup(search_name, upc="840243160563")
        self.assertEqual(result["chewy_url"], "https://chewy.example/blue-bits-tender-beef")
        self.assertEqual(result["chewy_rating"], 4.6)

    def test_lookup_gtin_match_bypasses_brand_conflict_gate(self):
        import chewy_lookup as cl
        item = {
            "Name": "Coolaroo Steel-Framed Elevated Dog Bed",
            "Manufacturer": "Coolaroo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/coolaroo-elevated-bed",
            "CurrentPrice": "55.99",
            "Gtin": "021234567890",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=None):
            result = cl.lookup("Gale Pacific Coolaroo The Original Cooling Elevated Dog Bed",
                               upc="21234567890")
        self.assertEqual(result["chewy_url"], "https://chewy.example/coolaroo-elevated-bed")

    def test_lookup_without_upc_argument_keeps_existing_behavior(self):
        # Backward compatibility: omitting upc must reproduce the pre-GTIN
        # REVIEW outcome for the same regression pair -- the fast path must
        # never activate implicitly.
        import chewy_lookup as cl
        search_name = (
            "Blue Buffalo Bits Beef Soft & Chewy Dog Treats, Bite-Sized for "
            "Training, Made with Real Beef & Enhanced with DHA, Heart-Shaped"
        )
        item = {
            "Name": "Blue Buffalo Blue Bits Tender Beef Dog Treats",
            "Manufacturer": "Blue Buffalo",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/blue-bits-tender-beef",
            "CurrentPrice": "41.99",
            "Gtin": "840243160563",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]):
            result = cl.lookup(search_name)
        self.assertTrue(str(result["chewy_url"]).startswith("REVIEW"))

    def test_lookup_upc_given_but_no_match_falls_back_to_scoring(self):
        # upc provided but doesn't match any candidate's Gtin -- must fall
        # through to the normal score/coverage/brand path unchanged.
        import chewy_lookup as cl
        search_name = "Fumoi Automatic Self-Cleaning Cat Litter Box, Large Capacity, App Control, Grey"
        item = {
            "Name": "Fumoi Automatic Self-Cleaning Cat Litter Box",
            "Manufacturer": "Fumoi",
            "StockAvailability": "InStock",
            "Url": "https://chewy.example/fumoi-litter-box",
            "CurrentPrice": "199.95",
            "Gtin": "111111111111",
        }
        with patch.object(cl, "ACCOUNT_SID", "x"), \
             patch.object(cl, "AUTH_TOKEN", "y"), \
             patch.object(cl, "search_catalog", return_value=[item]), \
             patch.object(cl, "scrape_chewy_rating", return_value=None):
            result = cl.lookup(search_name, upc="999999999999")
        self.assertEqual(result["chewy_url"], "https://chewy.example/fumoi-litter-box")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestChewyGtinMatch -v`
Expected: FAIL — `test_lookup_gtin_match_bypasses_low_coverage_downgrade` and `test_lookup_gtin_match_bypasses_brand_conflict_gate` fail with a `TypeError: lookup() got an unexpected keyword argument 'upc'`

- [ ] **Step 3: Implement the fast path**

Replace `find_best_match` (currently lines 276-297 of `chewy_lookup.py`) with:

```python
def find_best_match(product_name: str, upc: str | None = None) -> tuple[dict | None, int, bool]:
    """
    Try keyword variants in order. Return (best_item, score, gtin_matched)
    across all attempts. A GTIN hit against the known Amazon UPC (see
    _find_gtin_match) short-circuits immediately -- it's a definitive match,
    not a heuristic guess, so there's no reason to keep searching variants
    or scoring by name overlap. Otherwise stops early once score >= SCORE_AUTO_ACCEPT.
    """
    variants = _keyword_variants(product_name)
    best = None
    best_score = 0

    for kw in variants:
        print(f"[chewy_lookup] Trying: {kw!r}", file=sys.stderr)
        items = search_catalog(kw, page_size=10)
        if not items:
            continue
        if upc:
            gtin_hit = _find_gtin_match(items, upc)
            if gtin_hit:
                print(f"[chewy_lookup] GTIN match: {gtin_hit.get('Name','')[:70]}", file=sys.stderr)
                return gtin_hit, SCORE_AUTO_ACCEPT, True
        match, score = best_match(items, product_name)
        if match and score > best_score:
            best, best_score = match, score
            print(f"[chewy_lookup] Match score={score}: {match.get('Name','')[:70]}", file=sys.stderr)
            if best_score >= SCORE_AUTO_ACCEPT:
                break

    return best, best_score, False
```

Then replace `lookup` (currently lines 394-484) with:

```python
def lookup(product_name: str, upc: str | None = None) -> dict:
    """
    Full lookup. chewy_url sentinel logic:
      GTIN match (upc given, exact catalog hit)
                             -> full URL, rating scraped -- brand-conflict
                               and word-coverage gates are skipped entirely,
                               since an exact UPC match is definitive, not
                               a name-similarity guess
      >= SCORE_AUTO_ACCEPT, no brand conflict, coverage >= COVERAGE_AUTO_ACCEPT
                             -> full URL, rating scraped
      >= SCORE_AUTO_ACCEPT but brand conflict or low word coverage
                             -> "REVIEW:{url}" -- likely wrong brand or a
                               different pack size/variant, human verify
      >= SCORE_REVIEW        -> "REVIEW:{url}" -- low confidence, human verify
      < SCORE_REVIEW         -> "REVIEW" -- not found on Chewy
      credentials missing    -> all None
    """
    result = {
        "chewy_url":          None,
        "chewy_price":        None,
        "chewy_stock":        None,
        "chewy_rating":       None,
        "chewy_matched_name": None,  # auditing: what Chewy product was matched
    }

    if not ACCOUNT_SID or not AUTH_TOKEN:
        print("[chewy_lookup] IMPACT_ACCOUNT_SID or IMPACT_AUTH_TOKEN not set", file=sys.stderr)
        return result

    match, score, gtin_matched = find_best_match(product_name, upc)

    if not match or score < SCORE_REVIEW:
        print(f"[chewy_lookup] No match — setting REVIEW sentinel", file=sys.stderr)
        result["chewy_url"] = "REVIEW"
        return result

    raw_url    = match.get("Url") or None
    price      = match.get("CurrentPrice") or None
    stock      = match.get("StockAvailability") or None

    matched_name = match.get("Name", "")
    result["chewy_matched_name"] = matched_name

    if not gtin_matched:
        # Brand identity gate (see _first_brand_token at module level): if the
        # searched product has a recognisable brand token and it does not appear
        # anywhere in the matched Chewy product name, the match is likely a
        # category-similar product from a different brand. Cap the effective score
        # below SCORE_AUTO_ACCEPT so it is flagged for human review.
        searched_brand  = _first_brand_token(product_name)
        matched_brand   = _first_brand_token(matched_name)
        brand_conflict  = (
            searched_brand
            and matched_brand
            and searched_brand not in matched_name.lower()
            and matched_brand  not in product_name.lower()
        )
        if brand_conflict and score >= SCORE_AUTO_ACCEPT:
            print(
                f"[chewy_lookup] Brand mismatch: searched='{searched_brand}' matched='{matched_brand}' "
                f"— downgrading score {score} -> {SCORE_AUTO_ACCEPT - 1} (REVIEW)",
                file=sys.stderr
            )
            score = SCORE_AUTO_ACCEPT - 1  # force into REVIEW band

        # Word-coverage gate (see COVERAGE_AUTO_ACCEPT / _word_coverage): a
        # same-brand match can clear the score on common words alone while
        # actually being a different pack size or variant. Require the two
        # names to substantially overlap, not just share a brand + a few words,
        # before trusting the matched price enough to show it in the article.
        coverage = _word_coverage(product_name, matched_name)
        if coverage < COVERAGE_AUTO_ACCEPT and score >= SCORE_AUTO_ACCEPT:
            print(
                f"[chewy_lookup] Low word coverage ({coverage:.2f}) despite score={score} "
                f"— likely a different pack size/variant, not the same product "
                f"— downgrading to REVIEW",
                file=sys.stderr
            )
            score = SCORE_AUTO_ACCEPT - 1  # force into REVIEW band
    else:
        print(f"[chewy_lookup] GTIN-matched — skipping brand/coverage heuristics", file=sys.stderr)

    if score >= SCORE_AUTO_ACCEPT:
        print(f"[chewy_lookup] Auto-accepted (score={score}): {matched_name[:60]}", file=sys.stderr)
        result["chewy_url"]   = raw_url
        result["chewy_price"] = price
        result["chewy_stock"] = stock
        if raw_url:
            time.sleep(1)
            result["chewy_rating"] = scrape_chewy_rating(raw_url)
    else:
        # SCORE_REVIEW <= score < SCORE_AUTO_ACCEPT — flag for human review
        print(f"[chewy_lookup] Low confidence (score={score}): {matched_name[:60]} — flagging REVIEW", file=sys.stderr)
        result["chewy_url"]   = f"REVIEW:{raw_url}" if raw_url else "REVIEW"
        result["chewy_price"] = price
        result["chewy_stock"] = stock
        # No rating scrape for unverified matches

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k "TestChewyGtinMatch or TestChewyWordCoverage or TestChewyBrandGate or TestSilentLegRegressions" -v`
Expected: PASS (all tests in these 4 classes, including every pre-existing test — this confirms the change is additive and doesn't regress the score/coverage/brand-gate paths)

- [ ] **Step 5: Commit**

```bash
git add chewy_lookup.py test_pipeline.py
git commit -m "feat(chewy): auto-accept exact GTIN matches, bypassing brand/coverage gates"
```

---

### Task 4: Thread `upc` through `refill_products.py`

**Files:**
- Modify: `refill_products.py:413-441` (`chewy_enrich`, `apply_resolution`)
- Test: `test_pipeline.py` (add one test near `TestManualResolve`, since that's where `apply_resolution` is currently exercised)

- [ ] **Step 1: Write the failing test**

Add this method to the `TestManualResolve` class in `test_pipeline.py` (anywhere inside the class body, e.g. right after `test_happy_path_applies_and_writes`):

```python
    def test_apply_resolution_passes_upc_to_chewy_enrich(self):
        import refill_products as rp

        entry = {"topic": "best-x", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}
        resolved = {"name": "Some Product", "asin": "B0ABCD1234",
                    "image": "https://m.media-amazon.com/images/I/x.jpg",
                    "price": "9.99", "stars": 4.0, "upc": "810189030893"}
        with patch.object(rp, "chewy_enrich", return_value={
                "chewy_url": None, "chewy_price": None,
                "chewy_stock": None, "chewy_rating": None}) as fake_enrich:
            rp.apply_resolution(entry, resolved)
        fake_enrich.assert_called_once_with("Some Product", "810189030893")
        self.assertEqual(entry["upc"], "810189030893")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k test_apply_resolution_passes_upc_to_chewy_enrich -v`
Expected: FAIL — either a `TypeError` (chewy_enrich called with wrong arity) or `AssertionError` on `entry["upc"]` (KeyError, since `apply_resolution` doesn't set it yet)

- [ ] **Step 3: Implement the wiring**

Replace `chewy_enrich` and `apply_resolution` (currently lines 413-441 of `refill_products.py`) with:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k "TestManualResolve" -v`
Expected: PASS (all `TestManualResolve` tests, including the pre-existing ones — `resolved.get("upc")` is `None` when omitted, so `apply_resolution` behaves exactly as before for every existing caller)

- [ ] **Step 5: Commit**

```bash
git add refill_products.py test_pipeline.py
git commit -m "feat(refill): thread optional upc through chewy_enrich/apply_resolution"
```

---

### Task 5: Add `--upc` to `manual_resolve.py`

**Files:**
- Modify: `manual_resolve.py:44-71` (argparse + resolved dict)
- Test: `test_pipeline.py` (add two tests to `TestManualResolve`)

- [ ] **Step 1: Write the failing tests**

Add these two methods to `TestManualResolve`:

```python
    def test_upc_flag_is_optional_and_populates_entry_when_provided(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr
        import chewy_lookup as cl

        products = [{"topic": "best-automatic-litter-box",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path), \
                 patch.object(cl, "ACCOUNT_SID", ""), \
                 patch.object(cl, "AUTH_TOKEN", ""):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                    "--upc", "810189030893",
                ])
            written = json.loads(path.read_text())
        self.assertEqual(written[0]["upc"], "810189030893")

    def test_upc_omitted_leaves_entry_without_upc_key(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr
        import chewy_lookup as cl

        products = [{"topic": "best-automatic-litter-box",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path), \
                 patch.object(cl, "ACCOUNT_SID", ""), \
                 patch.object(cl, "AUTH_TOKEN", ""):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                ])
            written = json.loads(path.read_text())
        self.assertNotIn("upc", written[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k "test_upc_flag_is_optional or test_upc_omitted" -v`
Expected: FAIL — `error: unrecognized arguments: --upc 810189030893` (argparse `SystemExit`) for the first test; the second currently passes already (nothing to regress, but written to lock the contract in explicitly)

- [ ] **Step 3: Implement the flag**

In `manual_resolve.py`, add the argument after `parser.add_argument("--runners-up", dest="runners_up", default=None)`:

```python
    parser.add_argument("--upc", default=None,
                         help="Amazon UPC/GTIN, if visible in the product's "
                              "'Product information' section -- enables an "
                              "exact-match fast path in Chewy enrichment")
```

Then in the `resolved` dict construction, after the existing `runners_up` block:

```python
    resolved = {
        "name": args.name, "asin": args.asin, "image": args.image,
        "price": args.price, "stars": args.stars,
    }
    if args.runners_up:
        resolved["runners_up"] = args.runners_up
    if args.upc:
        resolved["upc"] = args.upc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestManualResolve -v`
Expected: PASS (all `TestManualResolve` tests)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `cd HappyPet && ./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: same pass/skip count as the pre-plan baseline (63 passed, 1 skipped, 2 pre-existing Windows-encoding failures — see `HANDOFF.md`) **plus** the new tests added in Tasks 1-5, all passing. No new failures.

- [ ] **Step 6: Update `manual_resolve.py`'s module docstring usage example**

Add `--upc` to the example invocation in the docstring at the top of `manual_resolve.py` (currently ends with `--runners-up "Litter-Robot 4; PetSafe ScoopFree"`):

```python
      --runners-up "Litter-Robot 4; PetSafe ScoopFree" \
      --upc 810189030893
```

- [ ] **Step 7: Commit**

```bash
git add manual_resolve.py test_pipeline.py
git commit -m "feat(manual-resolve): add optional --upc flag for exact Chewy GTIN matching"
```

---

## Explicitly out of scope (do not do these as part of this plan)

- **Backfilling UPCs for the 19 existing REVIEW-flagged `products.json` entries.** This plan only builds the mechanism. Re-running enrichment with real UPCs for those 19 is a manual follow-up (via `manual_resolve.py --upc` during a live-browser session), not automated here.
- **Extending `resolve_product()`'s automated Amazon-scrape path to capture UPC.** The search-results page it scrapes doesn't expose UPC; getting it would need either product-page scraping (new surface, new risk) or PA-API (parked). Out of scope.
- **The `_first_brand_token` "Gale" vs "Coolaroo" bug** noted separately during the REVIEW spot-check discussion. Real bug, but unrelated to GTIN matching — track and fix separately.
- **Any change to the Impact.com search/query strategy itself** (`_keyword_variants`, `search_catalog`). The GTIN check reuses whatever candidates the existing keyword search already returns; it does not query Impact.com any differently.

## Self-Review

**Spec coverage:** UPC capture in `manual_resolve.py` (Task 5), GTIN-based exact matching bypassing brand/coverage gates (Task 3), the two supporting pure helpers (Tasks 1-2), and end-to-end wiring through `refill_products.py` (Task 4) — all covered. Backward compatibility (no `upc` argument anywhere) is explicitly tested in Tasks 3-5 rather than assumed.

**Placeholder scan:** No TBD/TODO markers; every step has complete, runnable code and exact commands.

**Type consistency:** `find_best_match` return signature change (2-tuple → 3-tuple) is confined to `chewy_lookup.py` and is only ever unpacked in one place (`lookup()`, updated in the same task) — confirmed via `grep` that no test or other module calls `find_best_match` directly. `chewy_enrich(name, upc=None)` and `apply_resolution`'s call to it (`chewy_enrich(resolved["name"], resolved.get("upc"))`) match signatures across Tasks 3-4.
