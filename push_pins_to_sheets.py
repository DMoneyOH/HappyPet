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
# ---------------------------------------------------------------------------
_FB_HOOKS: dict[str, str] = {
    'joint-supplement':   "Stiff joints don't have to slow your dog down. These supplements actually work.",
    'dental-chew':        "Clean teeth, fresh breath, happy dog. The chews that actually deliver.",
    'calming-treat':      "Calm your dog without a vet visit. These treats actually soothe anxiety.",
    'anxiety-vest':       "Thunder, fireworks, separation -- these vests help your dog keep calm when it counts.",
    'probiotic':          "Gut health is everything. The right probiotic keeps your dog's digestion running smooth.",
    'puzzle-toy':         "Bored dogs are destructive dogs. These puzzle toys burn energy and keep them sharp.",
    'backpack-carrier':   "Carry your pup hands-free on any trail. Built for the long haul.",
    'life-jacket':        "Water adventures are safer with the right vest. Here's the one worth trusting.",
    'nail-grinder':       "No more nail-trim dread. This grinder is quiet, safe, and dog-approved.",
    'harness-leash':      "The right harness makes all the difference for outdoor cats. Here's what actually fits.",
    'window-perch':       "A sunny perch changes everything for an indoor cat. Here's the one worth buying.",
    'cat-bed':            "Cozy cats swear by these lounge-worthy beds -- find the perfect spot for your napper.",
    'kitten-food':        "Give your kitten the best start -- top-rated food for growth, immunity, and a shiny coat.",
    'calming-product':    "Stressed cats hide it well. These calming products actually make a difference.",
    'cat-litter':         "The secret to a mess-free litter area starts with the right box.",
    'puppy-food':         "Fuel your puppy's growth with food that actually delivers -- without filler.",
    'wet-cat-food':       "Picky cats devour this. Real food your feline will actually eat.",
    'dog-food':           "Dogs who eat well, live well. These top-rated foods make every meal count.",
    'flea':               "Flea season doesn't have to be a nightmare. Here's what actually works.",
    'tick':               "Keep ticks off your pet without harsh chemicals. Here's the smart way to protect them.",
    'shampoo':            "Bath time doesn't have to be a battle. The right shampoo makes all the difference.",
    'brush':              "A good brush is the foundation of a healthy coat. Here's the one groomers reach for.",
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

def _build_fb_message(slug: str, title: str, article_url: str) -> str:
    """Return a personable Facebook post message keyed to slug, fallback to title-derived hook."""
    clean_url = article_url.split('?')[0].rstrip('/') + '/'
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
import sys
import shutil
import smtplib
import time
from email.mime.text import MIMEText
import datetime as _dt
from pathlib import Path

REPO_DIR            = Path(__file__).parent
import sys as _sys; _sys.path.insert(0, str(REPO_DIR))
from json_io import atomic_write_json
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
QUEUE_LOW_THRESHOLD = 3
ALERT_FROM          = 'hello@happypetproductreviews.com'
ALERT_TO            = 'hello@happypetproductreviews.com'
SMTP_HOST           = 'smtp.gmail.com'
SMTP_PORT           = 587

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
    print(line, flush=True)
    with LOG_PATH.open('a') as f: f.write(line + chr(10))


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
        atomic_write_json(p, products, indent=2)
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
    p = REPO_DIR / 'products.json'
    if not p.exists():
        return 0
    products = json.loads(p.read_text())
    published = set()
    for md in (REPO_DIR / '_posts').glob('*.md'):
        if md.stem.startswith('DRAFT-'):
            published.add(md.stem[len('DRAFT-'):])  # pending counts as spoken-for
            continue
        parts = md.stem.split('-', 3)
        if len(parts) == 4:
            published.add(parts[3])
    return sum(1 for e in products if e.get('topic') not in published)


def send_queue_alert(unpublished_count: int) -> None:
    smtp_user  = os.environ.get('GMAIL_SMTP_USER', ALERT_FROM)
    smtp_login = os.environ.get('GMAIL_ACCOUNT', smtp_user)
    smtp_pass  = os.environ.get('GMAIL_APP_PASSWORD', '')
    if not smtp_pass:
        log('GMAIL_APP_PASSWORD not set -- skipping email alert', 'WARN')
        return
    subject = f'[HappyPet] Queue low: only {unpublished_count} unpublished articles remaining'
    body = (
        f'Happy Pet Product Reviews queue alert\n\n'
        f'Only {unpublished_count} unpublished article(s) remain in products.json.\n\n'
        f'Action needed: Add new topic entries to products.json before the queue runs dry.\n\n'
        f'Current unpublished topics:\n'
    )
    try:
        p = REPO_DIR / 'products.json'
        if p.exists():
            products = json.loads(p.read_text())
            published = set()
            for md in (REPO_DIR / '_posts').glob('*.md'):
                if md.stem.startswith('DRAFT-'):
                    published.add(md.stem[len('DRAFT-'):])
                    continue
                parts = md.stem.split('-', 3)
                if len(parts) == 4:
                    published.add(parts[3])
            for e in products:
                if e.get('topic') not in published:
                    body += f"  - {e.get('topic')} ({e.get('title', '')})\n"
    except Exception as _e:
        log(f'  Alert body build warning: {_e}', 'WARN')
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From']    = smtp_user
        msg['To']      = ALERT_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
            s.starttls()
            s.login(smtp_login, smtp_pass)
            s.sendmail(smtp_user, [ALERT_TO], msg.as_string())
        log(f'ALERT EMAIL sent to {ALERT_TO}')
    except Exception as e:
        log(f'ALERT EMAIL failed: {e}', 'ERROR')


def read_fb_queue_state(ws) -> tuple[set, _dt.date]:
    """
    Single read of the Facebook Queue sheet. Returns:
      - set of article URLs already queued (col B) -- the dedup authority
      - the first free SchedDate (latest existing + 1 day; tomorrow if empty)
    One read per run instead of one per pin, and callers increment the date
    per appended row so a batch never collides on the same day.
    Raises on read failure -- appending blind would create duplicates.
    """
    rows = sheets_retry('get_all_values', ws.get_all_values)
    urls  = set()
    dates = []
    for row in rows[1:]:  # skip header
        if len(row) > 1 and row[1].strip():
            urls.add(row[1].strip())
        if len(row) > 5 and row[5].strip():
            try:
                dates.append(_dt.date.fromisoformat(row[5].strip()))
            except ValueError:
                pass
    next_date = (max(dates) if dates else _dt.date.today()) + _dt.timedelta(days=1)
    if next_date <= _dt.date.today():
        next_date = _dt.date.today() + _dt.timedelta(days=1)
    return urls, next_date


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--slugs', default='', help='Comma-separated slugs to process')
    args = parser.parse_args()
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
        fb_ws    = sheets_retry('get_worksheet', fb_sheet.get_worksheet, 0)
    except Exception as open_exc:
        log(f'Could not open the FB Queue sheet: {open_exc} -- aborting', 'ERROR')
        sys.exit(1)

    # Dedup against the sheet itself, not against sent/ location. post_pins.py
    # used to move fired files into sent/ before this script ran, so the old
    # "name exists in sent/" check skipped every freshly pinned article and no
    # FB row was ever appended. The sheet is the only duplicate-proof record.
    try:
        queued_urls, sched_cursor = read_fb_queue_state(fb_ws)
    except Exception as read_exc:
        log(f'Could not read FB Queue sheet: {read_exc} -- aborting (appending blind would duplicate rows)', 'ERROR')
        sys.exit(1)
    log(f'FB Queue state: {len(queued_urls)} rows queued, next SchedDate={sched_cursor.isoformat()}')

    today     = _dt.date.today().isoformat()
    processed = 0
    failed    = 0
    alert_sent = False

    for qf in queue_files:
        try:
            data        = json.loads(qf.read_text())
            title       = data['title']
            article_url = data['article_url']
            image_url   = data.get('image_url', '')
            species     = data.get('species', 'both')
            slug        = data.get('slug', qf.stem)

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

            if article_url.strip() in queued_urls:
                log(f'SKIP (already in FB Queue sheet): {qf.name}')
                # Ensure the processed marker exists even if a prior run died
                # between append and move.
                _mark_processed(' (backfilled marker)')
                continue

            # image_url is written by generate_posts.py via build_pin_image_url_for_queue()
            # which guarantees ?v=YYYYMMDD is present. No late-append needed.
            # Build Facebook message -- personable hook derived from slug/title
            message = _build_fb_message(slug, title, article_url)

            # Next free schedule date from the cached read; advance per row so a
            # batch never lands multiple posts on the same day
            sched_date = sched_cursor.isoformat()

            # Append to Facebook Queue: Title, URL, Message, Image, OrigDate, SchedDate, Species, Posted, PostID
            fb_row = [title, article_url, message, image_url, today, sched_date, species, 'FALSE', '']
            fb_ws.append_row(fb_row)
            queued_urls.add(article_url.strip())
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
