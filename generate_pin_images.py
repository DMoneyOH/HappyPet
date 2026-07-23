#!/usr/bin/env python3
"""
generate_pin_images.py v3
- Uses repo fonts (assets/fonts/)
- Larger product image, auto-crops whitespace
- 1-sentence description below title
- Pillow-drawn arrow (no emoji)
- Pin generated BEFORE sheet append
- Hooked into generate_posts.py via make_pin_for_post()

GHA note: brain_secrets is vault-local and unavailable on GHA runners.
Fallback credentials path uses GCP_SA_KEY_B64 env var, same as all other pipeline scripts.
"""
import os, re, datetime, urllib.request, urllib.error
from pathlib import Path
from io import BytesIO

try:
    from dotenv import load_dotenv
    load_dotenv(Path.home() / '.env')
except ImportError:
    pass

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


def log_pin(msg: str, level: str = "INFO") -> None:
    line = f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [PINGEN]    [{level}]  {msg}"
    print(line, flush=True)


REPO      = Path(__file__).parent
import sys as _sys; _sys.path.insert(0, str(REPO))

# brain_secrets is vault-local and unavailable on GHA runners.
# Fallback: build sheets creds from GCP_SA_KEY_B64 env var (same as all other scripts).
try:
    from brain_secrets import get_sheets_creds, get_secret as brain_get_secret
except Exception:  # brain vault code can raise beyond ImportError; env-var fallback either way
    def brain_get_secret(key, *a, **kw): return os.environ.get(key, '')
    def get_sheets_creds():
        import base64, json as _j
        from google.oauth2.service_account import Credentials as _Creds
        info = _j.loads(base64.b64decode(os.environ['GCP_SA_KEY_B64']))
        return _Creds.from_service_account_info(
            info, scopes=['https://www.googleapis.com/auth/spreadsheets'])

PINS_DIR  = REPO / 'assets' / 'images' / 'pins'
POSTS_DIR = REPO / '_posts'
FONT_DIR  = REPO / 'assets' / 'fonts'
SITE_URL  = 'https://happypetproductreviews.com'

PINS_DIR.mkdir(parents=True, exist_ok=True)

PEACH = '#FFEEE4'; CORAL = '#FF6B4A'; TEAL = '#0D5C63'; SUN = '#FFD166'

def hex2rgb(h):
    h = h.lstrip('#')
    if len(h) == 3: h = ''.join(c*2 for c in h)
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

THEMES = [
    {'bg':PEACH, 'accent':CORAL, 'chip_bg':TEAL,  'chip_fg':SUN,    'title':TEAL,     'desc':TEAL,      'cta_bg':CORAL, 'cta_fg':'#FFF', 'brand':TEAL},
    {'bg':TEAL,  'accent':SUN,   'chip_bg':CORAL,  'chip_fg':'#FFF', 'title':'#FFFFFF','desc':'#C8E6E8', 'cta_bg':SUN,   'cta_fg':TEAL,   'brand':'#FFF'},
    {'bg':CORAL, 'accent':SUN,   'chip_bg':'#FFF', 'chip_fg':CORAL,  'title':'#FFFFFF','desc':'#FFD4CB', 'cta_bg':TEAL,  'cta_fg':'#FFF', 'brand':'#FFF'},
    {'bg':SUN,   'accent':TEAL,  'chip_bg':TEAL,   'chip_fg':SUN,    'title':TEAL,     'desc':TEAL,      'cta_bg':CORAL, 'cta_fg':'#FFF', 'brand':TEAL},
]

CAT_LABELS = {
    'cat-feeders':'Cat Feeders',   'cat-carriers':'Cat Carriers',
    'cat-litter':'Cat Litter',     'cat-scratching':'Cat Scratching',
    'dog-beds':'Dog Beds',         'dog-collars':'Dog Collars',
    'dog-toys':'Dog Toys',         'dog-harnesses':'Dog Harnesses',
    'pet-feeding':'Pet Feeding',   'dog-training':'Dog Training',
}
CTA_LABELS = {
    'cat-feeders':'Read the Review',   'cat-carriers':'See Our Pick',
    'cat-litter':'Read the Review',    'cat-scratching':'See Our Picks',
    'dog-beds':'Read the Review',      'dog-collars':'See Our Picks',
    'dog-toys':'See Our Picks',        'dog-harnesses':'Read the Review',
    'pet-feeding':'Read the Review',   'dog-training':'See Our Picks',
}

def get_font(name, size):
    for d in [FONT_DIR, Path('/tmp/happpet_fonts')]:
        p = d / name
        if p.exists():
            try: return ImageFont.truetype(str(p), size)
            except: pass
    return ImageFont.load_default()

def fetch_image(url):
    def _try(u):
        try:
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = r.read()
            if len(data) < 500:
                return None
            return Image.open(BytesIO(data)).convert('RGBA')
        except Exception:
            return None

    img = _try(url)
    if img:
        return img

    # Fallbacks for both Amazon CDN URL shapes. The pipeline passes
    # images-na.ssl-images-amazon.com/images/P/{ASIN} URLs (m.media-amazon.com
    # is blocked from GHA runner IPs), so the /images/P/ variants matter most;
    # the /images/I/ branch covers manually curated image URLs.
    m = re.search(r'/images/P/([A-Za-z0-9]+)', url)
    if m:
        asin = m.group(1).split('.')[0]
        for alt in [
            f'https://images-na.ssl-images-amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg',
            f'https://images-na.ssl-images-amazon.com/images/P/{asin}.01.LZZZZZZZ.jpg',
            f'https://images-na.ssl-images-amazon.com/images/P/{asin}.01.MZZZZZZZ.jpg',
        ]:
            if alt.split('?')[0] != url.split('?')[0]:
                img = _try(alt)
                if img:
                    return img

    m = re.search(r'/images/I/([A-Za-z0-9%+_-]+)[\._]', url)
    if m:
        for suffix in ['._AC_SL1500_.jpg', '._AC_SX522_.jpg', '.jpg']:
            alt = f'https://images-na.ssl-images-amazon.com/images/I/{m.group(1)}{suffix}'
            img = _try(alt)
            if img:
                return img

    log_pin(f'image fetch failed for {url[:80]} -- pin will render without product photo', 'WARN')
    return None

def autocrop_whitespace(img, threshold=240):
    diff = ImageOps.invert(img.convert('RGB'))
    bbox = diff.getbbox()
    if bbox:
        pad = 20
        x1 = max(0, bbox[0]-pad); y1 = max(0, bbox[1]-pad)
        x2 = min(img.width, bbox[2]+pad); y2 = min(img.height, bbox[3]+pad)
        return img.crop((x1, y1, x2, y2))
    return img

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ''
    for word in words:
        test = (current + ' ' + word).strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return lines

def get_stage_bg(img, threshold=235):
    w, h = img.size
    if w == 0 or h == 0 or w // 10 == 0 or h // 10 == 0:
        return (255, 255, 255)
    rgb = img.convert('RGB')
    samples = []
    for x in range(0, w, w//10):
        samples.append(rgb.getpixel((x, 0)))
        samples.append(rgb.getpixel((x, h-1)))
    for y in range(0, h, h//10):
        samples.append(rgb.getpixel((0, y)))
        samples.append(rgb.getpixel((w-1, y)))
    if not samples:
        return (255, 255, 255)
    avg = tuple(sum(c[i] for c in samples)//len(samples) for i in range(3))
    if all(v >= threshold for v in avg):
        return (255, 255, 255)
    return tuple(min(255, v + 20) for v in avg)

def draw_rounded_rect(draw, xy, radius, fill):
    x1, y1, x2, y2 = xy
    draw.rectangle([x1+radius, y1, x2-radius, y2], fill=fill)
    draw.rectangle([x1, y1+radius, x2, y2-radius], fill=fill)
    for cx, cy in [(x1,y1),(x2-2*radius,y1),(x1,y2-2*radius),(x2-2*radius,y2-2*radius)]:
        draw.ellipse([cx, cy, cx+2*radius, cy+2*radius], fill=fill)

def draw_arrow(draw, x, y, color, size=30):
    mid = y + size // 2
    draw.rectangle([x, mid-3, x+size-10, mid+3], fill=color)
    draw.polygon([(x+size-12, y+4), (x+size, mid), (x+size-12, y+size-4)], fill=color)

def make_pin(title, description, product_img_url, category, slug, theme_idx):
    if not PIL_AVAILABLE:
        log_pin('Pillow not available -- skipping pin generation', 'WARN')
        return None

    W, H = 1000, 1500
    t = THEMES[theme_idx % len(THEMES)]

    f_title = get_font('Fredoka-Bold.ttf', 78)
    f_desc  = get_font('Nunito-Bold.ttf', 36)
    f_chip  = get_font('Nunito-Bold.ttf', 30)
    f_cta   = get_font('Nunito-Bold.ttf', 36)
    f_brand = get_font('Fredoka-Bold.ttf', 30)

    dummy = Image.new('RGB', (W, H))
    ddraw = ImageDraw.Draw(dummy)
    title_lines = wrap_text(ddraw, title, f_title, W - 100)[:3]
    desc_lines  = wrap_text(ddraw, description, f_desc, W - 100)[:3] if description else []
    n_title = len(title_lines)
    n_desc  = len(desc_lines)

    TOP_BAR   = 90
    CHIP_H    = 62
    CHIP_GAP  = 36
    TITLE_H   = n_title * 90
    DESC_H    = n_desc * 50 + (20 if n_desc else 0)
    CTA_H     = 86
    BOTTOM    = 60
    text_zone = CHIP_GAP + CHIP_H + 16 + TITLE_H + DESC_H + 24 + CTA_H + BOTTOM
    IMG_H     = max(H - TOP_BAR - text_zone, 580)

    img  = Image.new('RGB', (W, H), hex2rgb(t['bg']))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, W, 16], fill=hex2rgb(t['accent']))
    draw.text((50, 30), 'happypetproductreviews.com', font=f_brand, fill=hex2rgb(t['brand']))

    IMG_Y = TOP_BAR
    prod_img = fetch_image(product_img_url) if product_img_url else None
    photo_embedded = prod_img is not None
    stage_bg = get_stage_bg(prod_img) if prod_img else (255, 255, 255)
    draw.rectangle([0, IMG_Y, W, IMG_Y + IMG_H], fill=stage_bg)

    if prod_img:
        prod_img = autocrop_whitespace(prod_img)
        PAD = 30
        scale = min((W-PAD*2)/prod_img.width, (IMG_H-PAD*2)/prod_img.height)
        nw, nh = int(prod_img.width*scale), int(prod_img.height*scale)
        prod_img = prod_img.resize((nw, nh), Image.LANCZOS)
        px = (W - nw) // 2
        py = IMG_Y + (IMG_H - nh) // 2
        if prod_img.mode == 'RGBA':
            img.paste(prod_img, (px, py), prod_img)
        else:
            img.paste(prod_img.convert('RGB'), (px, py))

    y = IMG_Y + IMG_H + CHIP_GAP

    cat_label = CAT_LABELS.get(category, category).upper()
    chip_w = int(draw.textlength(cat_label, font=f_chip)) + 56
    draw_rounded_rect(draw, [50, y, 50+chip_w, y+CHIP_H], 31, hex2rgb(t['chip_bg']))
    draw.text((50+28, y+13), cat_label, font=f_chip, fill=hex2rgb(t['chip_fg']))
    y += CHIP_H + 16

    for line in title_lines:
        draw.text((50, y), line, font=f_title, fill=hex2rgb(t['title']))
        y += 90

    if desc_lines:
        y += 6
        for line in desc_lines:
            draw.text((50, y), line, font=f_desc, fill=hex2rgb(t['desc']))
            y += 50

    y += 24
    cta_label  = CTA_LABELS.get(category, 'Read the Review')
    cta_text_w = int(draw.textlength(cta_label, font=f_cta))
    arrow_size = 30
    arrow_gap  = 18
    btn_w = cta_text_w + arrow_gap + arrow_size + 72
    draw_rounded_rect(draw, [50, y, 50+btn_w, y+76], 38, hex2rgb(t['cta_bg']))
    draw.text((50+36, y+18), cta_label, font=f_cta, fill=hex2rgb(t['cta_fg']))
    draw_arrow(draw, 50+36+cta_text_w+arrow_gap, y+(76-arrow_size)//2,
               hex2rgb(t['cta_fg']), size=arrow_size)

    url_text = 'happypetproductreviews.com'
    url_w = int(draw.textlength(url_text, font=f_brand))
    draw.text((W-50-url_w, H-44), url_text, font=f_brand,
              fill=(*hex2rgb(t['brand']), 120))

    out_path = PINS_DIR / f'{slug}.jpg'
    img.save(str(out_path), 'JPEG', quality=93)
    return photo_embedded  # True if the product photo was fetched + composited

def parse_posts():
    posts = []
    for fname in sorted(os.listdir(POSTS_DIR)):
        if not fname.endswith('.md'): continue
        text = open(POSTS_DIR / fname, encoding='utf-8').read()
        fm = {}
        m = re.match(r'^---\n(.*?)\n---', text, re.DOTALL)
        if m:
            for line in m.group(1).splitlines():
                if ':' in line:
                    k, _, v = line.partition(':')
                    fm[k.strip()] = v.strip().strip('"').strip("'")
        stem = fname.replace('.md','')
        if stem.startswith('DRAFT-'):
            # Drafts have no live URL and a standalone run would render their
            # pins under garbage slugs ('DRAFT-best-x' split as a date) and
            # push them to main -- skip until Stage 2 publishes them
            continue
        parts = stem.split('-',3)
        slug = parts[3] if len(parts)==4 else stem
        cat  = fm.get('categories','').strip('[]')
        posts.append({
            'title':       fm.get('title',''),
            'description': fm.get('description',''),
            'image':       fm.get('image',''),
            'species':     fm.get('species','both'),
            'cat':         cat,
            'slug':        slug,
            'url':         f'{SITE_URL}/{cat}/{slug}/',
        })
    return posts

def update_sheets(posts_with_pins):
    if not GSPREAD_AVAILABLE:
        log_pin('gspread not available -- skipping sheet update', 'WARN')
        return
    try:
        creds = get_sheets_creds()
    except Exception as _e:
        log_pin(f'sheets creds failed: {_e}', 'WARN'); return
    gc = gspread.Client(auth=creds)
    # Use brain_get_secret which falls back to os.environ on GHA
    DOG_ID = brain_get_secret('HAPPYPET_SHEET_ID_DOGS')
    CAT_ID = brain_get_secret('HAPPYPET_SHEET_ID_CATS')
    for label, sid, sp_filter in [('Dogs',DOG_ID,('dog','both')),('Cats',CAT_ID,('cat','both'))]:
        if not sid:
            log_pin(f'{label}: sheet ID not set -- skipping', 'WARN')
            continue
        sh   = gc.open_by_key(sid)
        ws   = sh.get_worksheet(0)
        rows = ws.get_all_values()
        updates = []
        for i, row in enumerate(rows[1:], start=2):
            if not row:
                continue  # trailing blank rows would IndexError after pins were rendered
            for p in posts_with_pins:
                if p['species'] in sp_filter and row[0] == p['title']:
                    updates.append({'range': f'C{i}', 'values': [[p['pin_url']]]})
                    break
        if updates:
            ws.batch_update(updates)
            log_pin(f'{label}: updated {len(updates)} pin URLs')

def make_pin_for_post(title, description, image_url, category, slug, theme_idx, strict=False):
    """Generate the pin image and return its hosted URL.

    Called tolerant (strict=False) by generate_posts.py's staging -- which may run
    in the Claude cloud container, where Amazon's image hosts are unreachable, so a
    photo-less pin is acceptable there because Stage 2 regenerates it. Called strict
    (strict=True) by the --slug CLI on the GHA runner (reachable network): a photo
    that STILL can't be fetched raises rather than shipping a blank pin."""
    if not PIL_AVAILABLE:
        log_pin('Pillow not available -- skipping pin image generation', 'WARN')
        return f'{SITE_URL}/assets/images/pins/{slug}.jpg'
    photo_embedded = make_pin(title, description, image_url, category, slug, theme_idx)
    if strict and not photo_embedded:
        raise RuntimeError(
            f'pin for {slug}: product photo could not be fetched from {image_url!r} '
            f'-- refusing to emit a photo-less pin')
    return f'{SITE_URL}/assets/images/pins/{slug}.jpg'

def main(update_sheets_flag=True):
    if not PIL_AVAILABLE:
        log_pin('Pillow not installed. Run: pip install Pillow --break-system-packages', 'ERROR')
        return []
    posts = parse_posts()
    log_pin(f'Found {len(posts)} posts')
    results = []
    for i, p in enumerate(posts):
        log_pin(f'[{i+1}/{len(posts)}] {p["title"][:55]}')
        make_pin(p['title'], p['description'], p['image'], p['cat'], p['slug'], i)
        pin_url = f'{SITE_URL}/assets/images/pins/{p["slug"]}.jpg'
        log_pin(f'  -> {pin_url}')
        results.append({**p, 'pin_url': pin_url})
    if update_sheets_flag:
        log_pin('\nUpdating Google Sheets...')
        update_sheets(results)
    log_pin('\nCommitting...')
    import subprocess as _sp
    _sp.run(['git', '-C', str(REPO), 'add', 'assets/images/pins/'], check=False)
    _sp.run(['git', '-C', str(REPO), 'commit', '-m', 'Regenerate branded Pinterest pin images'], check=False)
    _sp.run(['git', '-C', str(REPO), 'push', 'origin', 'main'], check=False)
    log_pin('Done.')
    return results

def regen_one(slug, strict=True):
    """Regenerate ONE published post's pin image from its front matter, on a host
    with a reachable network (the GHA runner). This exists because the cloud routine
    that first stages the post can't fetch Amazon product images, so its pin renders
    photo-less; Stage 2 calls this to overwrite it with a real one."""
    import zlib
    match = next((p for p in parse_posts() if p['slug'] == slug), None)
    if match is None:
        log_pin(f'regen: no published post found for slug {slug!r}', 'ERROR')
        raise SystemExit(2)
    theme_idx = zlib.crc32(slug.encode()) % len(THEMES)
    make_pin_for_post(match['title'], match['description'], match['image'],
                      match['cat'], slug, theme_idx, strict=strict)
    log_pin(f'regen: {slug} -> assets/images/pins/{slug}.jpg')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--slug', help='Regenerate one published post pin (strict) and exit')
    args = ap.parse_args()
    if args.slug:
        regen_one(args.slug)
    else:
        main()
