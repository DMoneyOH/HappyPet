#!/usr/bin/env python3
"""
push_pins_to_sheets.py
Reads staged _pin_queue/*.json files, appends rows to HappyPet Facebook Queue sheet,
then moves processed files to _pin_queue/sent/.
After each slug completes, retires it from products.json (rolling queue model).
When unpublished products.json count drops to 3, logs warning + sends alert email.
Pinterest posting is handled separately via post_pins.py -> IFTTT webhooks.
"""

# ---------------------------------------------------------------------------
# Facebook message hooks -- keyed by slug fragment, fallback to title-derived.
# Add new entries here as the article library grows rather than editing the
# append logic. Hooks must be second-person or third-person only -- no I/we/us/our/my.
# They follow the post copy rules: no crowd claims, no promises, no unsourced
# facts. Each one says what the post covers (test_pipeline.TestFbMessage).
# ---------------------------------------------------------------------------
_FB_HOOKS: dict[str, str] = {
    'joint-supplement':   "Joint supplements for dogs, compared by ingredient, dose and form.",
    'dental-chew':        "Dental chews for dogs: what to check before you buy.",
    'calming-treat':      "How calming treats for dogs differ: active ingredient, dose and form.",
    'anxiety-vest':       "Anxiety vests for dogs, compared by fit, fabric and sizing.",
    'probiotic':          "Probiotics for dogs: what to read on the label before you buy.",
    'puzzle-toy':         "How dog puzzle toys differ: difficulty, material and cleanup.",
    'backpack-carrier':   "Backpack carriers for dogs, compared by weight limit, straps and airflow.",
    'life-jacket':        "Life jackets for dogs: how to check fit, handles and sizing.",
    'nail-grinder':       "How dog nail grinders differ: speed settings, guards and charging.",
    'harness-leash':      "Harness and leash sets for cats, compared by fit and adjustment.",
    'window-perch':       "Window perches for cats: mount type, size and weight limit, side by side.",
    'cat-bed':            "Cat beds, compared by shape, size and washable covers.",
    'kitten-food':        "Kitten food: what to read on the label before you buy.",
    'calming-product':    "How calming products for cats differ, and how each one is used.",
    'cat-litter':         "Cat litter and litter boxes, compared by type, size and cleanup.",
    'puppy-food':         "Puppy food, compared by protein source and breed size.",
    'wet-cat-food':       "How wet cat foods differ: protein, texture and ingredients.",
    'dog-food':           "Dog food: what to read on the label before you buy.",
    'flea':               "Flea treatments for pets, compared by type and how often each is applied.",
    'tick':               "How tick treatments differ: type and how often each is applied.",
    'shampoo':            "Pet shampoos: ingredients and coat type, side by side.",
    'brush':              "Pet brushes, compared by bristle type and coat length.",
}

# Varied fallback hooks for slugs without a curated _FB_HOOKS entry, so
# uncurated posts don't all share one sentence. Selected deterministically by
# slug hash: a given slug always renders the same message (idempotent re-runs),
# different slugs spread across the pool. Keep these em-dash-free.
_FB_FALLBACK_TEMPLATES: list[str] = [
    "The best {topic}, without the endless scrolling. Here's our pick.",
    "We did the digging on {topic} so you don't have to.",
    "Shopping for {topic}? Here's where we landed.",
    "Our top pick for {topic}, after comparing the field.",
    "Everything we compared on {topic}, down to one clear winner.",
    "Cut through the noise on {topic}. Here's the one we'd buy.",
    "The {topic} actually worth your money. No fluff.",
    "Less guesswork, better {topic}. Here's what we'd get.",
]

# GA4 campaign tag for Facebook links, so Facebook visits are attributable.
# Mirrors the Pinterest tag generate_posts.build_url puts on article_url
# (utm_source=pinterest&utm_medium=social&utm_campaign=pin). Both the message
# (col C) and the row's URL (col B, the link the Apps Script posts) carry it.
# Col B is also the dedup key, so read_fb_queue_state() compares _dedup_key()
# forms, which every tag variant of one article shares.
FB_UTM = 'utm_source=facebook&utm_medium=social&utm_campaign=post'

def _page_url(url: str) -> str:
    """The file's one URL clean-up: scheme, host and path with one trailing
    slash; query and fragment dropped."""
    parts = urllib.parse.urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}/"

def _fb_url(article_url: str) -> str:
    """The article's page URL with the Facebook tag in place of any query."""
    return _page_url(article_url) + '?' + FB_UTM

def _build_fb_message(slug: str, title: str, article_url: str) -> str:
    """Return a personable Facebook post message keyed to slug, fallback to title-derived hook."""
    clean_url = _fb_url(article_url)
    # Try each hook key as a substring of the slug
    for key, hook in _FB_HOOKS.items():
        if key in slug:
            return f'\U0001f43e {hook}\n{clean_url}'
    # Fallback: strip the 'Best ' prefix, then pick a varied hook keyed to the
    # slug so uncurated posts don't all read the same. Deterministic per slug.
    import hashlib
    hook_title = title
    for prefix in ('Best ', 'Top ', 'The Best '):
        if hook_title.startswith(prefix):
            hook_title = hook_title[len(prefix):]
            break
    topic = hook_title.lower()
    idx = int(hashlib.md5(slug.encode('utf-8')).hexdigest(), 16) % len(_FB_FALLBACK_TEMPLATES)
    return f'\U0001f43e {_FB_FALLBACK_TEMPLATES[idx].format(topic=topic)}\n{clean_url}'

import argparse
import json
import os
import re
import sys
import shutil
import time
import urllib.parse
import datetime as _dt
from pathlib import Path

REPO_DIR            = Path(__file__).parent
import sys as _sys; _sys.path.insert(0, str(REPO_DIR))
from json_io import write_products
try:
    from brain_secrets import (get_sheets_creds as _vault_sheets_creds,
                               get_secret as _vault_get_secret,
                               BrainSecretsUnavailable)
except Exception:  # brain_secrets.py absent entirely (stripped checkout)
    _vault_get_secret = None
    _vault_sheets_creds = None
    class BrainSecretsUnavailable(RuntimeError):
        """Stand-in so the except clauses below stay valid. Nothing can raise
        the real one when brain_secrets did not import."""

def brain_get_secret(key, project="HappyPet"):
    """Resolve a secret: Brain vault first (local dev), then env var (CI/GitHub
    Secrets). brain_secrets.get_secret returns None on CI by design and imports
    cleanly there, so the env fallback must run at CALL time, not be gated on
    ImportError (which never fires when brain_secrets.py is present).

    BrainSecretsUnavailable is deliberately NOT swallowed: a vault that is
    configured here but cannot be unlocked means the credentials are
    misconfigured, not absent, and quietly using env values instead is how a
    run ends up "succeeding" with nothing in it."""
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

def get_sheets_creds():
    """Sheets creds: Brain vault first, then base64 service-account JSON in
    GCP_SA_KEY_B64 (the CI secret). Same vault-then-env contract as above --
    including that a misconfigured vault (BrainSecretsUnavailable) is a hard
    failure, not something to paper over with the CI fallback."""
    from google.oauth2.service_account import Credentials
    if _vault_sheets_creds is not None:
        try:
            return _vault_sheets_creds()
        except BrainSecretsUnavailable:
            raise
        except Exception:
            pass
    import base64, json as _j
    info = _j.loads(base64.b64decode(os.environ['GCP_SA_KEY_B64']))
    return Credentials.from_service_account_info(info,
        scopes=['https://www.googleapis.com/auth/spreadsheets'])
LOG_PATH            = REPO_DIR / 'LOGS' / f"HappyPet_{_dt.date.today().isoformat()}.log"
LOG_PATH.parent.mkdir(exist_ok=True)
QUEUE_LOW_THRESHOLD = 3   # sender, recipient and SMTP host live in queue_alert.py

# Transient Sheets API failures. On 2026-08-20 a single 503 from Google's
# frontend hit gc.open_by_key(), which sat outside every try block, and took the
# whole pin run down as an unhandled traceback -- so the article's Facebook row
# was never appended AND the workflow never reached its cleanup step, leaving the
# slug live in products.json and .pending-slugs. Same shape as
# chewy_lookup.API_MAX_RETRY / API_RETRY_WAIT.
SHEETS_MAX_RETRY       = 3    # total attempts, so 2 retries
SHEETS_RETRY_WAIT      = 2    # seconds before the first retry; doubles after
SHEETS_TRANSIENT_CODES = (429, 500, 502, 503, 504)


def log(msg: str, level: str = 'INFO') -> None:
    line = f"{_dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [SHEETS] [{level}]  {msg}"
    with LOG_PATH.open('a', encoding='utf-8') as f: f.write(line + chr(10))
    try:
        print(line, flush=True)
    except UnicodeEncodeError:   # cp1252 console on Windows: '?' beats a crash
        enc = getattr(sys.stdout, 'encoding', None) or 'ascii'
        print(line.encode(enc, 'replace').decode(enc), flush=True)


def _api_status(exc) -> int | None:
    """The HTTP status behind a gspread APIError, or None if there isn't one.

    Both sources are consulted because neither is reliable alone.
    APIError.code is parsed out of the JSON error body and is set to -1 when
    Google answers with HTML instead, which a frontend 503 often does;
    response.status_code is the transport's own answer and survives that.
    """
    for value in (getattr(exc, 'code', None),
                  getattr(getattr(exc, 'response', None), 'status_code', None)):
        if isinstance(value, int) and value > 0:
            return value
    return None


def sheets_retry(label: str, fn, *args, **kwargs):
    """Run an IDEMPOTENT Sheets call, retrying transient upstream failures.

    Reads only. append_row is deliberately NOT wrapped: a write that fails in
    transit may already have landed server-side, so retrying it can put a second
    row in the Facebook queue -- which this script's in-memory dedup cannot see,
    a later run cannot undo, and which posts twice. A stuck pipeline is
    recoverable; a duplicate post is not.

    Anything that is not a transient status -- a 403, a 404, a revoked service
    account -- is re-raised on the first attempt. A backoff that retries every
    APIError just delays the same failure by six seconds and is its own defect.
    """
    for attempt in range(1, SHEETS_MAX_RETRY + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            status = _api_status(exc)
            if status not in SHEETS_TRANSIENT_CODES or attempt == SHEETS_MAX_RETRY:
                raise
            wait = SHEETS_RETRY_WAIT * (2 ** (attempt - 1))
            log(f'  Sheets {label}: HTTP {status} -- retry {attempt}/'
                f'{SHEETS_MAX_RETRY - 1} in {wait}s', 'WARN')
            time.sleep(wait)


def load_env():
    env_path = Path.home() / '.env'
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())


def retire_from_products(slug: str) -> int:
    """Remove slug from products.json, archive to Brain. Returns remaining entry count."""
    p = REPO_DIR / 'products.json'
    if not p.exists():
        return 0
    products = json.loads(p.read_text())
    before   = len(products)
    retired  = [e for e in products if e.get('topic') == slug]
    products = [e for e in products if e.get('topic') != slug]
    if len(products) < before:
        # Atomic (temp file + os.replace), not a bare write_text: this is a
        # read-modify-write of products.json on an ephemeral CI runner that can be
        # cancelled or OOM-killed mid-step, and a truncated products.json takes the
        # whole pipeline down until a human repairs it. Same reason json_io exists.
        write_products(p, products)
        log(f'  RETIRED: {slug} removed from products.json ({before} -> {len(products)} entries)')
        try:
            import sqlite3 as _sq
            brain = Path.home() / 'vault' / 'maeve_brain.db'
            if brain.exists() and retired:
                e   = retired[0]
                now = _dt.datetime.now(_dt.timezone.utc).isoformat()
                con = _sq.connect(str(brain))

                # Archive retirement record
                con.execute(
                    """INSERT INTO products_archive
                        (topic, title, keyword, asin, affiliate_url, species, category, price, stars, retired_at, post_slug, project_name)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (e.get('topic',''), e.get('title',''), e.get('keyword',''),
                     e.get('asin',''), e.get('affiliate_url',''), e.get('species',''),
                     e.get('category',''), e.get('price',''), e.get('stars'),
                     now, slug, 'HappyPet')
                )

                # Resolve published_at from dated _posts/ filename
                pub_at = now  # fallback: retirement time if post file not found
                for md in (REPO_DIR / '_posts').glob(f'2*-{slug}.md'):
                    parts = md.stem.split('-', 3)
                    if len(parts) == 4:
                        pub_at = '-'.join(parts[:3]) + 'T12:00:00+00:00'
                        break

                # Write publication record (INSERT OR IGNORE -- idempotent if already backfilled)
                con.execute(
                    """INSERT OR IGNORE INTO published_articles
                        (slug, title, category, species, asin, affiliate_url, product_name,
                         keyword, price, stars, published_at, project_name)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (slug, e.get('title',''), e.get('category',''), e.get('species',''),
                     e.get('asin',''), e.get('affiliate_url',''), e.get('name',''),
                     e.get('keyword',''), e.get('price',''), e.get('stars'),
                     pub_at, 'HappyPet')
                )

                con.commit()
                con.close()
                log(f'  ARCHIVED: {slug} logged to Brain products_archive + published_articles (pub={pub_at[:10]})')
        except Exception as arc_e:
            log(f'  ARCHIVE WARN: {arc_e}', 'WARN')
    return len(products)


def count_unpublished() -> int:
    # The shared definition (queue_alert.py), so this alert and refill_watchdog
    # never disagree: only entries the next runs can actually publish count.
    import queue_alert
    return len(queue_alert.publishable_unpublished())


def send_queue_alert(unpublished_count: int) -> None:
    import queue_alert
    import generate_posts as gp
    subject = f'[HappyPet] Queue low: only {unpublished_count} unpublished articles remaining'
    body = (
        f'Happy Pet Product Reviews queue alert\n\n'
        f'Only {unpublished_count} unpublished article(s) remain in products.json.\n\n'
        f'Action needed: Add new topic entries to products.json before the queue runs dry.\n\n'
        f'Current unpublished topics:\n'
    )
    try:
        products = gp.load_products()
        for slug in queue_alert.publishable_unpublished(products):
            body += f"  - {slug} ({products[slug].get('title', '')})\n"
    except Exception as _e:
        log(f'  Alert body build warning: {_e}', 'WARN')
    sent, what = queue_alert.send_alert(subject, body)
    log(what, 'INFO' if sent else ('WARN' if 'NOT sent' in what else 'ERROR'))

def _dedup_key(url: str) -> str:
    """Col B's dedup form: the _page_url() host and path, with the scheme dropped
    (http and https match) and the host lowercased without a leading "www.".
    Rows from Apr 4-16 hold the clean URL, rows from Apr 19 the Pinterest-tagged
    one, new rows the Facebook-tagged one; every form of one article shares this
    key, so none is queued twice."""
    parts = urllib.parse.urlsplit(_page_url(url))
    return parts.netloc.lower().removeprefix('www.') + parts.path

def read_fb_queue_state(ws) -> tuple[set, _dt.date]:
    """
    Single read of the Facebook Queue sheet. Returns:
      - set of _dedup_key() keys of the rows already queued (col B) -- the
        dedup authority
      - the first free SchedDate (latest existing + 1 day; tomorrow if empty)
    One read per run instead of one per pin, and callers increment the date
    per appended row so a batch never collides on the same day.
    Raises on read failure -- appending blind would create duplicates.
    """
    rows = sheets_retry('get_all_values', ws.get_all_values)
    queued_keys = set()
    dates = []
    for row in rows[1:]:  # skip header
        if len(row) > 1 and row[1].strip():
            queued_keys.add(_dedup_key(row[1]))
        if len(row) > 5 and row[5].strip():
            try:
                dates.append(_dt.date.fromisoformat(row[5].strip()))
            except ValueError:
                pass
    next_date = (max(dates) if dates else _dt.date.today()) + _dt.timedelta(days=1)
    if next_date <= _dt.date.today():
        next_date = _dt.date.today() + _dt.timedelta(days=1)
    return queued_keys, next_date


# --repost: queue an already-live review for Facebook again. The row is the
# normal new-post row (main() below), with SchedDate set to the requested date.
REPOST_MIN_GAP_DAYS = 7   # any two rows for one slug must be at least this far apart
_SLUG_RE = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')


def _url_slug(url: str) -> str:
    """The last path segment of a col B URL. Matched on this, not _dedup_key(),
    so a row written before the post changed category still counts."""
    return urllib.parse.urlsplit(_page_url(url)).path.rstrip('/').rsplit('/', 1)[-1]


def repost_row(slug: str, sched_date: _dt.date, today: str) -> list:
    """The Facebook Queue row for a published post, built from its front matter."""
    import generate_posts as gp
    import categories
    from validate_published_chewy_links import parse_frontmatter
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(f'{slug!r} is not a post slug')
    posts = sorted((REPO_DIR / '_posts').glob(
        f'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{slug}.md'))
    if not posts:
        raise ValueError(f'{slug}: no published post in _posts/')
    category = categories.post_category(posts[0])
    if not category:
        raise ValueError(f'{slug}: post has no category, so no URL')
    fm = parse_frontmatter(posts[0])
    title = fm.get('title') or slug
    article_url = gp.build_url(slug, category=category)
    return [title, _fb_url(article_url), _build_fb_message(slug, title, article_url),
            product_image_url(slug), today, sched_date.isoformat(),
            fm.get('species') or 'both', 'FALSE', '']


def plan_reposts(rows: list, requests: list, today: str) -> list:
    """Validate every (slug, YYYY-MM-DD) request against the sheet's rows and
    return the rows to append. Raises ValueError on the first bad request, so a
    batch is written whole or not at all. A request is refused when its date is
    not after today, or when the slug already has a row (in the sheet or earlier
    in this batch) less than REPOST_MIN_GAP_DAYS away, which covers the same
    slug and date. Another post on the same day is allowed and logged."""
    today_d = _dt.date.fromisoformat(today)
    slug_dates: dict[str, list] = {}
    for row in rows[1:]:  # skip header
        if len(row) > 5 and row[1].strip():
            try:
                d = _dt.date.fromisoformat(row[5].strip())
            except ValueError:
                continue
            slug_dates.setdefault(_url_slug(row[1]), []).append(d)
    planned = []
    for slug, raw in requests:
        try:
            d = _dt.date.fromisoformat(raw)
        except ValueError:
            raise ValueError(f'{slug}: {raw!r} is not a YYYY-MM-DD date') from None
        if d <= today_d:
            raise ValueError(f'{slug}: {d} is not after today ({today})')
        near = [x for x in slug_dates.get(slug, []) if abs((d - x).days) < REPOST_MIN_GAP_DAYS]
        if near:
            raise ValueError(f'{slug}: already queued for {near[0]}, less than '
                             f'{REPOST_MIN_GAP_DAYS} days from {d}')
        if any(d in dates for dates in slug_dates.values()):
            log(f'  {d} already has another post queued; {slug} will post the same day', 'WARN')
        planned.append(repost_row(slug, d, today))
        slug_dates.setdefault(slug, []).append(d)
    return planned


def queue_reposts(ws, requests: list, dry_run: bool) -> None:
    """--repost: one sheet read, validate the whole batch, then append it."""
    from refill_products import eastern_date
    try:
        rows = sheets_retry('get_all_values', ws.get_all_values)
        planned = plan_reposts(rows, requests, eastern_date())
    except Exception as exc:
        log(f'REPOST refused, nothing written: {exc}', 'ERROR')
        sys.exit(1)
    for n, row in enumerate(planned):
        if dry_run:
            log(f'DRY RUN: would append SchedDate={row[5]} {row[1]}')
            continue
        try:
            ws.append_row(row)   # never retried: see the sheets_retry docstring
        except Exception as exc:
            log(f'REPOST append failed at {row[1]} ({exc}); {n} of {len(planned)} '
                f'appended before it -- check the sheet before re-running', 'ERROR')
            sys.exit(1)
        log(f'FB QUEUE: repost appended {row[1]} -> SchedDate={row[5]}')
    log(f'DONE -- {len(planned)} repost(s) {"checked (dry run)" if dry_run else "appended"}')


def product_image_url(slug: str) -> str:
    """The image the Facebook Queue row carries: the PRODUCT, never the pin.

    The pin (queue JSON image_url) is a 1000x1500 portrait made for Pinterest;
    Facebook crops or distorts it. This mirrors the site's one image rule,
    _includes/social-image.html (the post's og:image, and the buy panel's pick):
    the self-hosted thumbnail when the post has one, else the post's own `image`
    front matter made absolute, else the landscape brand card. A test renders the
    include beside this function so the two cannot drift.
    """
    import generate_posts as gp
    from validate_published_chewy_links import parse_frontmatter
    pick = None
    if (REPO_DIR / 'assets' / 'images' / 'thumbs' / f'{slug}.jpg').is_file():
        pick = f'/assets/images/thumbs/{slug}.jpg'
    else:
        # Exact-slug match: _posts/YYYY-MM-DD-<slug>.md, so 'x-<slug>' cannot match.
        for post in sorted((REPO_DIR / '_posts').glob(
                f'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{slug}.md')):
            pick = parse_frontmatter(post).get('image')
            if pick:
                break
    pick = pick or '/assets/images/og-image.png'
    # Liquid's absolute_url: an absolute URL passes through, a path gets the site.
    return pick if re.match(r'[a-z][a-z0-9+.-]*://', pick, re.I) else gp.SITE_BASE + pick


def open_fb_worksheet(gspread, fb_sheet_id: str):
    """The Facebook Queue worksheet, or exit(1) with a logged reason."""
    try:
        creds = get_sheets_creds()
        gc    = gspread.Client(auth=creds)
    except Exception as creds_exc:
        log(f"Sheets creds failed: {creds_exc} -- cannot append to FB Queue", "ERROR")
        sys.exit(1)

    # Retried and caught. Both of these used to sit bare: the 2026-08-20 503
    # surfaced as a raw traceback, which reads as a code fault rather than the
    # upstream outage it was, and skipped the "Sheets creds failed" style of
    # message every other failure here produces.
    try:
        fb_sheet = sheets_retry('open_by_key', gc.open_by_key, fb_sheet_id)
        return sheets_retry('get_worksheet', fb_sheet.get_worksheet, 0)
    except Exception as open_exc:
        log(f'Could not open the FB Queue sheet: {open_exc} -- aborting', 'ERROR')
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slugs', default='', help='Comma-separated slugs to process')
    parser.add_argument('--repost', nargs=2, action='append', metavar=('SLUG', 'YYYY-MM-DD'),
                        help='Queue a published post for Facebook again on that date. '
                             'Repeat for more rows. Skips the pin queue entirely.')
    parser.add_argument('--dry-run', action='store_true',
                        help='With --repost: read the sheet and check, write nothing')
    args = parser.parse_args()
    if args.repost and args.slugs:
        parser.error('--repost and --slugs are separate runs')
    if args.dry_run and not args.repost:
        parser.error('--dry-run only applies to --repost')
    slug_filter = set(s.strip() for s in args.slugs.split(',') if s.strip())

    load_env()

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        log('gspread not installed. Run: pip install gspread google-auth --break-system-packages', 'ERROR')
        sys.exit(1)

    fb_sheet_id = (brain_get_secret('FACEBOOK_QUEUE_SHEET_ID') or os.environ.get('FACEBOOK_QUEUE_SHEET_ID', '')).strip()
    if not fb_sheet_id:
        log('FACEBOOK_QUEUE_SHEET_ID not set -- cannot append to Facebook Queue', 'ERROR')
        sys.exit(1)

    if args.repost:
        queue_reposts(open_fb_worksheet(gspread, fb_sheet_id), args.repost, args.dry_run)
        return

    queue_dir = REPO_DIR / '_pin_queue'
    sent_dir  = queue_dir / 'sent'
    queue_dir.mkdir(exist_ok=True)
    sent_dir.mkdir(exist_ok=True)

    queue_files = sorted(queue_dir.glob('*.json')) + sorted(sent_dir.glob('*.json'))
    if slug_filter:
        queue_files = [f for f in queue_files if f.stem in slug_filter]
        log(f'Slug filter active -- processing {len(queue_files)} file(s): {slug_filter}')
    if not queue_files:
        log('No queued pins found -- nothing to do')
        return

    fb_ws = open_fb_worksheet(gspread, fb_sheet_id)

    # Dedup against the sheet itself, not against sent/ location. post_pins.py
    # used to move fired files into sent/ before this script ran, so the old
    # "name exists in sent/" check skipped every freshly pinned article and no
    # FB row was ever appended. The sheet is the only duplicate-proof record.
    try:
        queued_keys, sched_cursor = read_fb_queue_state(fb_ws)
    except Exception as read_exc:
        log(f'Could not read FB Queue sheet: {read_exc} -- aborting (appending blind would duplicate rows)', 'ERROR')
        sys.exit(1)
    log(f'FB Queue state: {len(queued_keys)} rows queued, next SchedDate={sched_cursor.isoformat()}')

    from refill_products import eastern_date
    today     = eastern_date()   # OrigDate in ET, like pulled_on
    processed = 0
    failed    = 0
    alert_sent = False

    for qf in queue_files:
        try:
            data        = json.loads(qf.read_text())
            title       = data['title']
            article_url = data['article_url']
            species     = data.get('species', 'both')
            slug        = data.get('slug', qf.stem)
            # Product image, not data['image_url'] (the portrait Pinterest pin).
            image_url   = product_image_url(slug)

            def _mark_processed(note=''):
                """Move the queue file into sent/ (this script's marker). Handles
                the file already living in sent/ and stale root+sent duplicates."""
                if qf.parent == sent_dir:
                    return
                if (sent_dir / qf.name).exists():
                    qf.unlink()
                    log(f'CLEANED: stale duplicate {qf.name} removed from queue root{note}')
                else:
                    shutil.move(str(qf), str(sent_dir / qf.name))
                    log(f'SENT: {qf.name} -> _pin_queue/sent/{note}')

            if _dedup_key(article_url) in queued_keys:
                log(f'SKIP (already in FB Queue sheet): {qf.name}')
                # Ensure the processed marker exists even if a prior run died
                # between append and move.
                _mark_processed(' (backfilled marker)')
                continue

            # Build Facebook message -- personable hook derived from slug/title
            message = _build_fb_message(slug, title, article_url)

            # Next free schedule date from the cached read; advance per row so a
            # batch never lands multiple posts on the same day
            sched_date = sched_cursor.isoformat()

            # Append to Facebook Queue: Title, URL, Message, Image, OrigDate, SchedDate, Species, Posted, PostID
            fb_row = [title, _fb_url(article_url), message, image_url, today, sched_date, species, 'FALSE', '']
            fb_ws.append_row(fb_row)
            queued_keys.add(_dedup_key(article_url))
            sched_cursor += _dt.timedelta(days=1)
            log(f'FB QUEUE: appended {slug} -> SchedDate={sched_date}')

            retire_from_products(slug)

            _mark_processed()

            if not alert_sent:
                unpub = count_unpublished()
                if unpub <= QUEUE_LOW_THRESHOLD:
                    log(f'QUEUE LOW: {unpub} unpublished article(s) remain -- add new topics!', 'WARN')
                    send_queue_alert(unpub)
                    alert_sent = True

            processed += 1

        except Exception as e:
            log(f'FAIL: {qf.name} -- {e}', 'ERROR')
            failed += 1

    log(f'DONE -- {processed} appended to FB Queue, {failed} failed')


if __name__ == '__main__':
    main()
