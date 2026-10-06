#!/usr/bin/env python3
"""
post_pins.py - HappyPet Pinterest poster via IFTTT Maker webhooks
Sheets role: NONE. This script fires webhooks and writes dedup sentinels; it
touches no spreadsheet. The only live sheet is the Facebook Queue, appended by
push_pins_to_sheets.py, which owns that path end to end.

The six per-category "topical" sheets (FOOD/HEALTH/HOME/TOYS/CATS/DOGS) were
retired; the column-F = YES marking that used to run here was removed with them
(it had been failing 404 on every run and swallowing the error into a WARN).
The HAPPYPET_SHEET_ID_* strings that remain below are CATEGORY LABELS carried in
products.json's `topical_sheet` field, not spreadsheet IDs -- see TOPICAL_EVENT.

Dedup contract (single-owner markers):
  _pin_queue/.fired/{slug}.fired          -> ALL events fired (this script's marker)
  _pin_queue/.fired/{slug}.{event}.fired  -> that one webhook succeeded (partial-failure retry)
  _pin_queue/sent/                        -> owned by push_pins_to_sheets.py (FB-queued marker);
                                             this script must NEVER move files there.

Board routing -- species board(s) plus exactly one category board:
  species=cat/both -> happypet_pin_cats
  species=dog/both -> happypet_pin_dogs
  FOOD   -> happypet_pin_food
  HEALTH -> happypet_pin_health
  HOME   -> happypet_pin_home
  TOYS   -> happypet_pin_toys
  anything else (a DOGS/CATS species label, empty, unknown) -> happypet_pin_home

Payload: a JSON body {"image_url", "title", "description", "source_url"} POSTed
to IFTTT's json endpoint, one per board, as event happypet_pinjson_<board>
(JSON_EVENT maps each board to it). Each applet's filter code parses the body
and sets the Pinterest fields one to one.

title       = the queue title, capped at 100 chars (Pinterest's title limit).
description = the caption printed on the pin image, capped at 800 chars.
              Never "title | description": a3fab21 removed that concatenation
              because it overran the 100-char title cap. The description now has
              its own field.

The caption comes from the published post's front-matter `description`, read
by generate_pin_images.parse_posts() -- the same parser regen_one() uses when
publish.yml re-renders the final pin on the runner. The queue file's own
`description` is only the fallback: content sweeps edit front matter, not queue
files, and 8 of the first 41 queue copies had drifted from the image.

Board identity (resolve_events, the per-event .fired sentinels) stays keyed on
the happypet_pin_<board> names; only the URL uses the json event name. Renaming
the sentinels would make a half-fired slug re-pin its finished boards.

test_pipeline.py::TestPinJsonPayload pins this behavior.

Usage:
  python3 post_pins.py
  python3 post_pins.py --slugs a,b
  python3 post_pins.py --dry-run
"""

import argparse, datetime as _dt, json, os, sys, time
import urllib.error, urllib.request
from pathlib import Path

REPO_DIR  = Path(__file__).parent.resolve()
import sys as _sys; _sys.path.insert(0, str(REPO_DIR))
try:
    from brain_secrets import (get_secret as _vault_get_secret,
                               BrainSecretsUnavailable)
except Exception:  # brain_secrets.py absent entirely (stripped checkout)
    _vault_get_secret = None
    class BrainSecretsUnavailable(RuntimeError):
        """Stand-in so the except clauses below stay valid. Nothing can raise
        the real one when brain_secrets did not import."""

def brain_get_secret(key, project="HappyPet"):
    """Resolve a secret: Brain vault first (local dev), then env var (CI/GitHub
    Secrets). brain_secrets.get_secret returns None on CI by design and the
    module imports cleanly there, so the env fallback must run at CALL time --
    gating it on ImportError (the old shape) left it dead, and pins failed with
    'IFTTT_MAKER_KEY not set' though the key was in the environment.

    BrainSecretsUnavailable is deliberately NOT swallowed: it means a vault is
    configured on this host but cannot be unlocked, i.e. the credentials are
    misconfigured rather than absent. Falling through to env there is what
    produced a "successful" run with an empty audit trail."""
    if _vault_get_secret is not None:
        try:
            val = _vault_get_secret(key, project)
        except BrainSecretsUnavailable:
            raise
        except Exception:
            val = None
        if val:
            return val
    return os.environ.get(key, '')

LOG_PATH  = REPO_DIR / "LOGS" / f"HappyPet_{_dt.date.today().isoformat()}.log"
LOG_PATH.parent.mkdir(exist_ok=True)

MAKER_URL = "https://maker.ifttt.com/trigger/{event}/json/with/key/{key}"

# Board event -> the JSON-trigger applet event for that board. The one place the
# old names map to the new; fire_webhook raises KeyError on anything else.
JSON_EVENT = {
    "happypet_pin_dogs":   "happypet_pinjson_dogs",
    "happypet_pin_cats":   "happypet_pinjson_cats",
    "happypet_pin_food":   "happypet_pinjson_food",
    "happypet_pin_health": "happypet_pinjson_health",
    "happypet_pin_home":   "happypet_pinjson_home",
    "happypet_pin_toys":   "happypet_pinjson_toys",
}

TITLE_MAX = 100   # Pinterest pin title limit
DESC_MAX  = 800   # Pinterest pin description limit

# Category label (products.json `topical_sheet`) -> IFTTT event. The keys are
# named after the retired topical spreadsheets purely because that is the string
# products.json already carries; nothing here opens a spreadsheet. Renaming them
# would mean migrating products.json and refill_products.VALID_SHEETS in step.
#
# Only the four CATEGORY boards belong here. The DOGS/CATS labels are species,
# not categories: mapping them to the species events (524f161) made a dog post
# labelled DOGS fire happypet_pin_dogs twice and reach one board instead of two.
# Species boards come from the `species` field alone -- see resolve_events.
TOPICAL_EVENT = {
    "HAPPYPET_SHEET_ID_FOOD":   "happypet_pin_food",
    "HAPPYPET_SHEET_ID_HEALTH": "happypet_pin_health",
    "HAPPYPET_SHEET_ID_HOME":   "happypet_pin_home",
    "HAPPYPET_SHEET_ID_TOYS":   "happypet_pin_toys",
}
# Pet Home & Lifestyle: the broadest of the four, used when a queue file's label
# is not a category (a DOGS/CATS species label, empty, null, unknown).
DEFAULT_CATEGORY_EVENT = "happypet_pin_home"

MAX_RETRIES  = 3
BACKOFF_BASE = 15
RPM_SLEEP    = 2


def log(msg, level="INFO"):
    line = f"{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [POSTPINS] [{level}]  {msg}"
    print(line, flush=True)
    with LOG_PATH.open("a") as f:
        f.write(line + "\n")


def http_post(url, payload, headers, *, label):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            # 429 and 5xx are transient (IFTTT blips) -- retry; 4xx is a real error
            if exc.code != 429 and exc.code < 500:
                raise RuntimeError(f"{label} HTTP {exc.code}: {body[:200]}")
            if attempt == MAX_RETRIES:
                break  # no point sleeping before the terminal raise
            wait = BACKOFF_BASE * (2 ** attempt)
            log(f"  {label} HTTP {exc.code} attempt {attempt}/{MAX_RETRIES} -- wait {wait}s", "WARN")
            time.sleep(wait)
        except urllib.error.URLError as exc:
            if attempt == MAX_RETRIES:
                break
            log(f"  {label} network error attempt {attempt}: {exc.reason}", "WARN")
            time.sleep(RPM_SLEEP * 3)
    raise RuntimeError(f"{label} exhausted after {MAX_RETRIES} attempts")


def cap_text(text, limit):
    """Cap at `limit` chars, cutting at the last whitespace so no word is split.
    A single word longer than the limit is hard-cut (nothing better exists)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    if text[limit].isspace():          # the cut lands between words
        return text[:limit].rstrip()
    parts = text[:limit].rsplit(None, 1)   # drop the word the cut split
    return parts[0].rstrip() if len(parts) == 2 else text[:limit]


def build_payload(image_url, title, description, source_url):
    return {
        "image_url":   image_url,
        "title":       cap_text(title, TITLE_MAX),
        "description": cap_text(description, DESC_MAX),
        "source_url":  source_url,
    }


def load_pin_captions():
    """slug -> the caption drawn on that post's pin image. Imported here, not at
    module level: generate_pin_images loads ~/.env and creates the pins dir on
    import, which nothing else in this script should pay for."""
    import generate_pin_images
    return {p["slug"]: p["description"] for p in generate_pin_images.parse_posts()}


def fire_webhook(event, payload, maker_key):
    json_event = JSON_EVENT[event]
    url     = MAKER_URL.format(event=json_event, key=maker_key)
    body    = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    try:
        result = http_post(url, body, headers, label=f"Maker:{json_event}")
        log(f"  FIRED {json_event} -- {result.strip()[:80]}")
        return True
    except Exception as exc:
        log(f"  FAIL {json_event} -- {exc}", "ERROR")
        return False


def resolve_events(species, topical_sheet):
    """Species board(s) + exactly one category board, never a duplicate.

    A label that is not one of the four categories is remapped to the default
    category rather than rejected: the remap still lands on the species board(s)
    and one category board, and the WARN names the bad label. It was chosen when
    this script exited 0 on a failed pin, so a rejection was a silent no-pin. It
    now exits 1 on a failed pin; whether to keep the remap is an open decision."""
    events = []
    # Same as the label below: case and edge whitespace don't change the species.
    species = species.strip().lower() if isinstance(species, str) else species
    if species in ("dog", "both"):
        events.append("happypet_pin_dogs")
    if species in ("cat", "both"):
        events.append("happypet_pin_cats")
    if not events:
        log(f"  WARN: unknown species='{species}' -- falling back to happypet_pin_dogs", "WARN")
        events.append("happypet_pin_dogs")
    # Case and edge whitespace don't change which category a label names.
    label = topical_sheet.strip().upper() if isinstance(topical_sheet, str) else topical_sheet
    category = TOPICAL_EVENT.get(label)
    if category is None:
        log(f"  WARN: topical_sheet='{topical_sheet}' is not a category label -- "
            f"using {DEFAULT_CATEGORY_EVENT}", "WARN")
        category = DEFAULT_CATEGORY_EVENT
    events.append(category)
    return events


def build_pin_image_url_for_ifttt(image_url: str) -> str:
    """Return bare image URL with no query string for IFTTT/Pinterest delivery.
    Pinterest CDN rejects URLs containing query parameters. Strip all query strings
    before firing to IFTTT. This is the enforced contract for all IFTTT payloads.
    """
    if not image_url:
        return image_url
    return image_url.split("?")[0]


def check_url_live(url: str, timeout: int = 8) -> bool:
    """Return True if URL responds 200. Skips check if url is empty."""
    if not url:
        return False
    try:
        import urllib.request
        req = urllib.request.Request(url, method="HEAD",
                                     headers={"User-Agent": "HappyPetBot/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200
    except Exception as e:
        log(f"  WARN: URL check failed for {url[:60]} -- {e}", "WARN")
        return False


def check_image_has_content(url: str, min_bytes: int = 10000) -> bool:
    """Verify image URL returns real image content, not a placeholder.
    Pinterest silently drops pins with broken/placeholder images.
    min_bytes=10000 -- any real product photo is well above this threshold."""
    if not url:
        return False
    try:
        import urllib.request
        bare_url = url.split("?")[0]
        req = urllib.request.Request(bare_url, headers={"User-Agent": "HappyPetBot/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = r.read()
        if len(data) < min_bytes:
            log(f"  WARN: image too small ({len(data)} bytes) -- likely placeholder: {bare_url[:60]}", "WARN")
            return False
        return True
    except Exception as e:
        log(f"  WARN: image content check failed for {url[:60]} -- {e}", "WARN")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--slugs",   default="", help="Comma-separated slugs (empty = all queued)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force",   action="store_true", help="Re-fire even if already in sent/")
    args = parser.parse_args()

    maker_key = (brain_get_secret("IFTTT_MAKER_KEY", "global") or "").strip()
    if not maker_key:
        log("IFTTT_MAKER_KEY not set in Brain vault_secrets", "ERROR")
        sys.exit(1)

    slug_filter = set(s.strip() for s in args.slugs.split(",") if s.strip()) if args.slugs else set()

    queue_dir  = REPO_DIR / "_pin_queue"
    sent_dir   = queue_dir / "sent"
    fired_dir  = queue_dir / ".fired"
    queue_dir.mkdir(exist_ok=True)
    sent_dir.mkdir(exist_ok=True)
    fired_dir.mkdir(exist_ok=True)

    queue_files = sorted(queue_dir.glob("*.json"))
    if slug_filter:
        # Exact stem match -- same semantics as push_pins_to_sheets.py, so one
        # --slugs value behaves identically in both scripts (substring matching
        # once fired best-cat-tree-large when only best-cat-tree was requested)
        queue_files = [f for f in queue_files if f.stem in slug_filter]

    # A requested slug with no queue file and no all-events sentinel was never
    # pinned: its queue file is gone (e.g. moved to sent/ after a failed fire)
    # or never existed. That used to exit 0 as "nothing to do".
    queued_stems = {f.stem for f in queue_files}
    missing = sorted(s for s in slug_filter
                     if s not in queued_stems and not (fired_dir / f"{s}.fired").exists())
    for s in missing:
        log(f"MISSING: requested slug '{s}' has no queue file and no .fired sentinel", "ERROR")

    if not queue_files:
        log("No queued pins found -- nothing to do")
        if missing:
            sys.exit(1)
        return

    log(f"START -- {len(queue_files)} pin(s){' [DRY RUN]' if args.dry_run else ''}")

    captions = load_pin_captions()
    processed = 0
    failed    = 0

    for qf in queue_files:
        try:
            fired_sentinel = fired_dir / f"{qf.stem}.fired"
            if fired_sentinel.exists() and not args.force:
                log(f"SKIP (already fired): {qf.name}")
                continue
            if fired_sentinel.exists() and args.force:
                log(f"FORCE: re-firing {qf.name} (already fired)", "WARN")

            data        = json.loads(qf.read_text())
            slug        = data.get("slug", qf.stem)
            title       = data.get("title", slug)
            article_url = data.get("article_url", "")
            image_url   = build_pin_image_url_for_ifttt(data.get("image_url", ""))
            species     = data.get("species", "both")
            topical     = data.get("topical_sheet", "")

            caption = captions.get(slug)
            if caption is None:
                caption = data.get("description", "")
                log(f"  WARN: no published post for {slug} -- using the queue "
                    f"file's description as the caption", "WARN")
            payload = build_payload(image_url, title, caption, article_url)

            events = resolve_events(species, topical)
            if not events:
                log(f"WARN: no events for {slug} (species={species} topical={topical})", "WARN")
                failed += 1
                continue

            # Per-event sentinels: a prior partial failure (e.g. 2 of 3 webhooks
            # succeeded) must not re-fire the events that already went through --
            # that duplicated pins on every retry.
            def _ev_sentinel(event):
                return fired_dir / f"{qf.stem}.{event}.fired"
            pending_events = events if args.force else [e for e in events if not _ev_sentinel(e).exists()]

            log(f"PIN [{slug}] -> {events} (to fire: {pending_events or 'none, finalizing'})")
            log(f"  image: {image_url[:80]}")
            log(f"  url:   {article_url[:80]}")
            log(f"  title: {payload['title'][:80]}")
            log(f"  desc:  {payload['description'][:80]}")

            if args.dry_run:
                log("  DRY RUN -- skipping")
                processed += 1
                continue

            if pending_events:
                if not check_url_live(article_url):
                    log(f"  SKIP: article not live yet ({article_url[:60]})", "WARN")
                    failed += 1
                    continue

                if not check_image_has_content(image_url):
                    log(f"  ABORT: pin image has no content -- refusing to fire blank pin for {slug}", "ERROR")
                    failed += 1
                    continue

            pin_ok = True
            for event in pending_events:
                ok = fire_webhook(event, payload, maker_key)
                if ok:
                    _ev_sentinel(event).write_text(_dt.datetime.now(_dt.timezone.utc).isoformat())
                else:
                    pin_ok = False
                time.sleep(RPM_SLEEP)

            if pin_ok:
                # Write the all-events sentinel and commit immediately -- survives
                # GHA workspace recreation and prevents duplicate fires.
                # NOTE: the queue JSON stays where it is. Moving it to sent/ is
                # push_pins_to_sheets.py's marker (FB queued) -- when this script
                # moved it first, the FB Queue append silently never happened.
                try:
                    fired_sentinel.write_text(_dt.datetime.now(_dt.timezone.utc).isoformat())
                    import subprocess as _sp
                    git_steps = [
                        (['git', '-C', str(REPO_DIR), 'add', '_pin_queue/.fired'], 'add'),
                        (['git', '-C', str(REPO_DIR), 'commit', '-m',
                          f'chore: mark {slug} as fired (dedup sentinel)'], 'commit'),
                        (['git', '-C', str(REPO_DIR), 'push', 'origin', 'main'], 'push'),
                    ]
                    for cmd, step in git_steps:
                        r = _sp.run(cmd, capture_output=True, text=True)
                        if r.returncode != 0:
                            # A rejected push here is recoverable (the consume step
                            # re-pushes), but it must be visible, not swallowed.
                            log(f"  WARN: sentinel git {step} failed (rc={r.returncode}): "
                                f"{(r.stderr or r.stdout).strip()[:150]}", "WARN")
                            break
                    else:
                        log(f"  FIRED sentinel committed: _pin_queue/.fired/{qf.stem}.fired")
                except Exception as _fe:
                    log(f"  WARN: could not commit .fired sentinel: {_fe}", "WARN")
                processed += 1
            else:
                log(f"  PARTIAL failure for {slug} -- fired events recorded, will retry the rest", "WARN")
                failed += 1

        except Exception as exc:
            log(f"FAIL: {qf.name} -- {exc}", "ERROR")
            failed += 1

    log(f"DONE -- {processed} pinned, {failed} failed, {len(missing)} missing")
    # Nonzero so pin.yml's run goes red and its failure email fires; pin.yml
    # sets continue-on-error on this step so the Sheets and consume steps still run.
    if failed or missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
