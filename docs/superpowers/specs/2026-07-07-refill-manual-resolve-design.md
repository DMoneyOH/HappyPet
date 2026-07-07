# Refill Stage 0: Manual (Browser-Driven) Product Resolution — Design

*Written: 2026-07-07*

## Context

`refill_products.py`'s Amazon resolution (`resolve_product()` / `fetch_search_html()`)
is an anonymous `urllib` request to Amazon's public mobile search endpoint. As of
2026-07-07 it is fully blocked: a forced local run against all 21 `NEEDS_ASIN`/
`NEEDS_IMAGE` placeholders resolved 0/21, and the block hit *within* a single run
(first 6 requests got real search HTML but failed to parse a card; requests 7-21
got a ~2.3KB stub page with 0 `asin` attributes — a bot-check, not a parsing gap).
PA-API keys (the coded, unblocked path) are not yet available.

Git history (`f0e88b9`, `aff0f27`, and the `253f6fb` commit body) shows the
original, working design was never an anonymous scrape at all: a human/agent
browsed Amazon while logged into Associates Central, used the SiteStripe toolbar
for the affiliate link, and read the ASIN + image straight from the rendered DOM.
That traffic pattern never got blocked because it's a normal authenticated
browsing session, not a bot pattern. This design revives that approach, adapted
to the schema and automation (`chewy_lookup.py` Chewy enrichment) that didn't
exist yet when the original commits were made.

## Goal

Let a placeholder product topic get resolved by a live, browser-driven session
(me, using the claude-in-chrome tools against Derek's actual logged-in Chrome),
while reusing 100% of the existing validation and Chewy-enrichment code so the
two concrete historical bugs — a scraper price-parsing bug (`70253fe`) and bad
image-URL guessing (fixed by `f0e88b9`) — can't recur silently.

## Non-goals

- No headless/unattended browser automation (Playwright, saved-session cookies).
  Rejected: requires persisting Amazon session state as a new durable secret,
  and go-live is already gated on Derek regardless, so unattended resolution
  isn't the current bottleneck. Revisit only if full pipeline autonomy becomes
  the near-term goal.
- No change to `chewy_lookup.py` or the Chewy Impact.com API integration — it
  already works and needs no browser involvement.
- No change to `apply_resolution()`'s affiliate-URL construction (long-form
  `?tag=` link from the ASIN) — functionally equivalent to a SiteStripe short
  link for tracking purposes; not worth reintroducing the short-link format.
- No new persisted schema field for review count — it's a live judgment input,
  not stored data.

## Architecture

Three steps, only the first of which is new:

1. **Discovery (new, live, human-in-the-loop):** For a placeholder topic (e.g.
   `best-automatic-litter-box`), I use the claude-in-chrome tools to search
   Amazon from Derek's logged-in session, apply the selection criteria below,
   and read `name`, `asin`, `image` (the real `m.media-amazon.com/images/I/...`
   CDN URL), `price`, `stars`, and optionally 1-2 runner-up product names
   straight off the rendered page.
2. **Apply (new script, thin, reuses existing code):** `manual_resolve.py`
   takes what I found and reuses `refill_products.py`'s existing
   `validate_candidate()` (ASIN shape regex, image-host regex, "Sponsored"-name
   rejection) and `apply_resolution()` (which already calls `chewy_enrich()`
   and builds the affiliate URL) to validate, enrich, and write the entry into
   `products.json`. No Amazon or Chewy network code is duplicated.
3. **Ship (unchanged):** `refill/<date>` branch, commit `products.json`, push,
   open a PR for Derek's review — the same convention already used for PR #35
   and every prior automated refill PR.

## `manual_resolve.py`

```
python3 manual_resolve.py --topic best-automatic-litter-box \
  --name "..." --asin B0XXXXXXXX \
  --image https://m.media-amazon.com/images/I/....jpg \
  --price 199.99 --stars 4.5 \
  [--runners-up "Alt Product A; Alt Product B"]
```

Behavior per invocation:
1. Load `products.json`, find the entry by `--topic` (error if not found or not
   currently a placeholder).
2. Build a card dict `{name, asin, image}` and run it through
   `validate_candidate()`. On failure, print the specific reason (bad ASIN
   shape / wrong image host / sponsored-prefixed name) and exit non-zero
   without writing anything.
3. Build the `resolved` dict `{name, asin, image, price, stars, runners_up}`
   and call `apply_resolution(entry, resolved)` — this builds the affiliate
   URL and calls `chewy_enrich()` internally, unchanged.
4. Write `products.json` back, print a one-line confirmation.

Supports multiple `--topic` invocations in one process run (loop), or one
process per topic — implementation detail, not load-bearing.

Git branch/commit/push/PR stays a manual step I run afterward, same as PR #35.
Not scripted — the batch size varies per session and this is a low-frequency,
already-familiar action.

## Selection criteria (applied live, judgment-based, not persisted)

- Prefer ≥4.0 stars with a substantial review count for the category (no hard
  numeric threshold — a gut-check against what's typical for that product
  type).
- Reject obvious price outliers for the article's framing (e.g. a suspiciously
  cheap knockoff turning up in a "best premium X" roundup).
- Prefer "Ships from and sold by Amazon" / Fulfilled-by-Amazon listings over
  unclear third-party marketplace sellers.

## Testing

Add to `test_pipeline.py` (new cases alongside the existing `TestRefillAgent`
coverage, reusing its mocking patterns):
- `manual_resolve.py` rejects a bad ASIN shape (no write occurs).
- Rejects a non-`m.media-amazon.com` image host.
- Rejects a "Sponsored"-prefixed name.
- Happy path: valid inputs produce a correctly-written `products.json` entry
  with Chewy fields populated (mock `chewy_enrich`/`chewy_lookup.lookup`, don't
  hit the real Impact.com API in tests).

## Documentation

A short operational runbook, `docs/refill-manual-resolve.md`, gets written
during implementation (not part of this spec) covering the step-by-step
process and the criteria above, so a future session — mine or a fresh one —
can pick this up cold. `HANDOFF.md` gets a one-line pointer to it.
