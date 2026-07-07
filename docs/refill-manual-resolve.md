# Manual Product Resolution (Browser-Driven)

Amazon's anonymous-scrape resolution in `refill_products.py` is blocked (see
`docs/superpowers/specs/2026-07-07-refill-manual-resolve-design.md` for the
full history and evidence). Until PA-API keys are available, resolve
`NEEDS_ASIN`/`NEEDS_IMAGE` placeholders this way instead:

## Process

1. Pick a placeholder topic from `products.json` (any entry with
   `"asin": "NEEDS_ASIN"` or `"image": "NEEDS_IMAGE"`).
2. In a live Claude Code session with the claude-in-chrome tools connected to
   a Chrome logged into Amazon Associates Central, search Amazon using the
   entry's `amazon_search_query` field if present, otherwise its `keyword`
   field (mirrors the fallback `refill_products.py` itself uses).
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
   git checkout main
   git pull
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
