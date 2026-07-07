# Manual (Browser-Driven) Product Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `manual_resolve.py`, a small CLI that lets a browser-found Amazon product (name/ASIN/image/price/stars, found via a live logged-in session) be applied to a `products.json` placeholder through the existing validation and Chewy-enrichment code, with no Amazon or Chewy network code duplicated.

**Architecture:** One new file (`manual_resolve.py`) that imports `refill_products` and calls its existing `load_products()`, `validate_candidate()`, and `apply_resolution()` (which already calls `chewy_enrich()` internally). One new test class in the existing `test_pipeline.py`. One new operational runbook doc.

**Tech Stack:** Python 3.12, stdlib `argparse`/`json`/`pathlib`, `unittest` (existing suite, no new test framework).

See design rationale: [`docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md`](../specs/2026-07-07-refill-manual-resolve-design.md).

---

### Task 1: `manual_resolve.py` script + tests

**Files:**
- Create: `manual_resolve.py`
- Test: `test_pipeline.py` (new `TestManualResolve` class, appended after `TestRefillAgent`)

- [ ] **Step 1: Write the failing tests**

Add this class to `test_pipeline.py`, placed immediately after the `TestRefillAgent` class (find it with `grep -n "class TestRefillAgent" test_pipeline.py` — it currently ends right before `class TestChewyLookup` or similar; insert the new class there so it stays grouped with the other refill-stage tests):

```python
class TestManualResolve(unittest.TestCase):
    """manual_resolve.py -- apply a browser-found product to a products.json
    placeholder, reusing refill_products.py's validate_candidate/
    apply_resolution/chewy_enrich. No Amazon or Chewy network code lives here."""

    def test_happy_path_applies_and_writes(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{
            "topic": "best-automatic-litter-box",
            "title": "Best Automatic Litter Boxes",
            "keyword": "best automatic litter box",
            "species": "cat", "category": "cat-gear", "format": "roundup",
            "topical_sheet": "HAPPYPET_SHEET_ID_CATS",
            "name": "NEEDS_ASIN placeholder for best automatic litter box",
            "asin": "NEEDS_ASIN",
            "affiliate_url": "https://www.amazon.com/dp/NEEDS_ASIN?tag=pawpicks04-20",
            "image": "NEEDS_IMAGE", "price": None, "stars": None,
            "chewy_url": None, "chewy_price": None,
            "chewy_stock": None, "chewy_rating": None,
        }]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path):
                mr.main([
                    "--topic", "best-automatic-litter-box",
                    "--name", "PETLIBRO Automatic Self-Cleaning Litter Box",
                    "--asin", "B0ABCD1234",
                    "--image", "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg",
                    "--price", "249.99", "--stars", "4.5",
                    "--runners-up", "Litter-Robot 4; PetSafe ScoopFree",
                ])
            written = json.loads(path.read_text())

        entry = written[0]
        self.assertEqual(entry["asin"], "B0ABCD1234")
        self.assertEqual(entry["name"], "PETLIBRO Automatic Self-Cleaning Litter Box")
        self.assertEqual(entry["image"],
                          "https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg")
        self.assertEqual(entry["price"], "249.99")
        self.assertEqual(entry["stars"], 4.5)
        self.assertEqual(entry["runners_up"], "Litter-Robot 4; PetSafe ScoopFree")
        self.assertEqual(entry["affiliate_url"],
                          "https://www.amazon.com/dp/B0ABCD1234?tag=pawpicks04-20")
        # chewy_enrich runs for real here (IMPACT_* creds unset in test env),
        # which returns an all-None dict cleanly -- must not crash
        self.assertIsNone(entry["chewy_url"])

    def test_rejects_bad_asin_shape_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-ramps", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-dog-ramps", "--name", "Some Ramp",
                        "--asin", "NOTREAL123",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "39.99", "--stars", "4.2",
                    ])
            self.assertEqual(path.read_text(), original, "rejected candidate must not write")

    def test_rejects_wrong_image_host_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-cat-carrier-backpacks",
                     "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-cat-carrier-backpacks", "--name", "Some Carrier",
                        "--asin", "B0ABCD1234", "--image", "https://example.com/evil.jpg",
                        "--price", "59.99", "--stars", "4.3",
                    ])
            self.assertEqual(path.read_text(), original)

    def test_rejects_sponsored_prefixed_name_and_does_not_write(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-catnip-toys", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            original = path.read_text()
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-catnip-toys",
                        "--name", "Sponsored Ad - Fancy Catnip Toy",
                        "--asin", "B0ABCD1234",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "12.99", "--stars", "4.1",
                    ])
            self.assertEqual(path.read_text(), original)

    def test_rejects_unknown_topic(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-ramps", "asin": "NEEDS_ASIN", "image": "NEEDS_IMAGE"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "does-not-exist", "--name", "X",
                        "--asin", "B0ABCD1234",
                        "--image", "https://m.media-amazon.com/images/I/x.jpg",
                        "--price", "9.99", "--stars", "4.0",
                    ])

    def test_rejects_already_resolved_topic(self):
        import tempfile
        import refill_products as rp
        import manual_resolve as mr

        products = [{"topic": "best-dog-cooling-mat", "asin": "B0GG8LR3RW",
                     "image": "https://m.media-amazon.com/images/I/existing.jpg"}]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "products.json"
            path.write_text(json.dumps(products))
            with patch.object(rp, "PRODUCTS_PATH", path):
                with self.assertRaises(SystemExit):
                    mr.main([
                        "--topic", "best-dog-cooling-mat", "--name", "X",
                        "--asin", "B0NEWNEWNE",
                        "--image", "https://m.media-amazon.com/images/I/y.jpg",
                        "--price", "9.99", "--stars", "4.0",
                    ])
```

Note: `json`, `Path`, and `patch` are already imported at the top of `test_pipeline.py` (lines 18-22) — don't re-import them inside the test methods, only the per-test `tempfile`, `refill_products`, and `manual_resolve` imports shown above (matching the existing inline-import style used by `TestRefillAgent`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd HappyPet && .venv/Scripts/python.exe -m pytest test_pipeline.py -k TestManualResolve -v`

Expected: FAIL for every test with `ModuleNotFoundError: No module named 'manual_resolve'` (the file doesn't exist yet).

- [ ] **Step 3: Write the implementation**

Create `manual_resolve.py`:

```python
#!/usr/bin/env python3
"""
manual_resolve.py -- apply a manually-found Amazon product to a products.json
placeholder entry.

Amazon resolution in refill_products.py is currently blocked (anonymous
scrape returning 0/21 resolved as of 2026-07-07 -- see
docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md). This
script takes a product found via a live, logged-in browser session and
applies it through refill_products.py's existing validation and Chewy
enrichment -- no Amazon or Chewy network code is duplicated here.

Usage:
    python3 manual_resolve.py --topic best-automatic-litter-box \
      --name "PETLIBRO Automatic Self-Cleaning Litter Box" \
      --asin B0ABCD1234 \
      --image https://m.media-amazon.com/images/I/71abcXYZ._AC_SX425_.jpg \
      --price 249.99 --stars 4.5 \
      --runners-up "Litter-Robot 4; PetSafe ScoopFree"

Exits non-zero and writes nothing if:
  - --topic doesn't match an existing products.json entry
  - the matched entry is not currently a NEEDS_ASIN/NEEDS_IMAGE placeholder
  - the candidate fails refill_products.validate_candidate() (bad ASIN shape,
    wrong image host, or a "Sponsored"-prefixed name)
"""
import argparse
import json

import refill_products as rp


def find_placeholder(products: list, topic: str) -> dict:
    entry = next((e for e in products if e.get("topic") == topic), None)
    if entry is None:
        raise SystemExit(f"no products.json entry with topic '{topic}'")
    if entry.get("asin") != "NEEDS_ASIN" and entry.get("image") != "NEEDS_IMAGE":
        raise SystemExit(
            f"'{topic}' is not a NEEDS_ASIN/NEEDS_IMAGE placeholder "
            f"(asin={entry.get('asin')!r}, image={entry.get('image')!r})")
    return entry


def main(argv: list | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--asin", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--price", required=True)
    parser.add_argument("--stars", required=True, type=float)
    parser.add_argument("--runners-up", dest="runners_up", default=None)
    args = parser.parse_args(argv)

    products = rp.load_products()
    entry = find_placeholder(products, args.topic)

    card = {"name": args.name, "asin": args.asin, "image": args.image}
    if not rp.validate_candidate(card):
        raise SystemExit(
            f"REJECTED '{args.topic}': failed validate_candidate "
            f"(bad ASIN shape, wrong image host, or sponsored-prefixed name)")

    resolved = {
        "name": args.name, "asin": args.asin, "image": args.image,
        "price": args.price, "stars": args.stars,
    }
    if args.runners_up:
        resolved["runners_up"] = args.runners_up

    rp.apply_resolution(entry, resolved)
    rp.PRODUCTS_PATH.write_text(json.dumps(products, indent=2) + "\n")
    print(f"APPLIED '{args.topic}': {args.name} ({args.asin})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd HappyPet && .venv/Scripts/python.exe -m pytest test_pipeline.py -k TestManualResolve -v`
Expected: PASS, 6/6.

- [ ] **Step 5: Run the full suite to check for regressions**

Run: `cd HappyPet && .venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: same pass/fail counts as the pre-existing baseline (45 passed, 1 skipped, 2 failed — the 2 failures are the pre-existing Windows cp1252 encoding issue documented in `HANDOFF.md`, unrelated to this change) plus the 6 new tests passing, i.e. 51 passed, 1 skipped, 2 failed.

- [ ] **Step 6: Commit**

```bash
cd HappyPet
git add manual_resolve.py test_pipeline.py
git commit -m "$(cat <<'EOF'
Add manual_resolve.py: apply browser-found products to placeholders

Reuses refill_products.py's validate_candidate/apply_resolution/
chewy_enrich unchanged -- no new Amazon or Chewy network code. Lets
a placeholder get resolved from a live logged-in browser session
(see docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md)
while keeping the same validation gates the automated path already has.
EOF
)"
```

---

### Task 2: Operational runbook + HANDOFF pointer

**Files:**
- Create: `docs/refill-manual-resolve.md`
- Modify: `HANDOFF.md` (add one pointer line; exact insertion point depends on HANDOFF.md's state when this task runs — insert under whichever section currently discusses the placeholder backlog, e.g. near "Broken / blocked" or "Exact next action")

- [ ] **Step 1: Write the runbook**

Create `docs/refill-manual-resolve.md`:

```markdown
# Manual Product Resolution (Browser-Driven)

Amazon's anonymous-scrape resolution in `refill_products.py` is blocked (see
`docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md` for the
full history and evidence). Until PA-API keys are available, resolve
`NEEDS_ASIN`/`NEEDS_IMAGE` placeholders this way instead:

## Process

1. Pick a placeholder topic from `products.json` (any entry with
   `"asin": "NEEDS_ASIN"` or `"image": "NEEDS_IMAGE"`).
2. In a live Claude Code session with the claude-in-chrome tools connected to
   a Chrome logged into Amazon Associates Central, search Amazon for the
   entry's `keyword` field.
3. Pick the best candidate against these criteria:
   - Prefer >=4.0 stars with a review count that looks substantial for the
     category (no fixed threshold -- use judgment).
   - Reject obvious price outliers for the article's framing (e.g. a
     suspiciously cheap knockoff in a "best premium X" roundup).
   - Prefer "Ships from and sold by Amazon" / Fulfilled-by-Amazon listings
     over unclear third-party marketplace sellers.
4. Read directly off the rendered page: the product name, the ASIN (from the
   URL or page details), the image URL (must be `m.media-amazon.com/images/I/
   ...` -- the real CDN host, not a guessed one), the price, and the star
   rating. Optionally note 1-2 runner-up product names.
5. Apply it:

   ```bash
   cd HappyPet
   .venv/Scripts/python.exe manual_resolve.py --topic <topic> \
     --name "<product name>" --asin <ASIN> \
     --image <image URL> --price <price> --stars <stars> \
     [--runners-up "<alt 1>; <alt 2>"]
   ```

   This validates the candidate and runs the existing Chewy Impact.com
   lookup automatically -- reject output means fix the input and retry, not
   force a bad value through.
6. Repeat for as many topics as planned for the session (batch size varies
   per session, no fixed target).
7. Ship it the same way every refill PR ships:

   ```bash
   git checkout -b refill/$(date +%Y-%m-%d-%H%M)
   git add products.json
   git commit -m "auto: manual refill $(date +%Y-%m-%d)"
   git push -u origin HEAD
   gh pr create --repo DMoneyOH/HappyPet --base main \
     --title "Refill: manually resolved products $(date +%Y-%m-%d)" \
     --body "Resolved via live browser session (Amazon scrape is blocked, see docs/refill-manual-resolve.md). Review each product before merging."
   ```

## Do not

- Do not re-attempt `refill_products.py`'s anonymous scrape hoping for better
  luck -- it is blocked, not flaky (see the design doc for evidence).
- Do not hand-edit `products.json` directly -- always go through
  `manual_resolve.py` so the ASIN-shape/image-host/sponsored-name gates run.
```

- [ ] **Step 2: Add the HANDOFF.md pointer**

Read `HANDOFF.md` first (`cat HappyPet/HANDOFF.md`) to find its current "Broken / blocked" or equivalent section discussing the placeholder backlog, and add one line there pointing to the new runbook, e.g.:

```
- Manual resolution process for these placeholders: see `docs/refill-manual-resolve.md`.
```

- [ ] **Step 3: Commit**

```bash
cd HappyPet
git add docs/refill-manual-resolve.md HANDOFF.md
git commit -m "docs: add manual product-resolution runbook, link from HANDOFF"
```

---

## After Task 2

Both tasks land on branch `claude/happypet-recovery-15-manual-resolve` (already checked out, with the design spec as the first commit). Open the PR once both tasks are done and verified — do not merge; this is a review-first change like every other HappyPet PR.
