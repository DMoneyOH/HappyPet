"""No-repeat ledger: which (post, image) pairs have already been pinned.

MIRRORED FILE. An identical copy lives in the HappyPet repo (key = post slug,
used by post_pins.py) and in the vault's pinterest-storage-venture (key =
ASIN, used by daily_dispatch.py). The two repos cannot import each other, so
the module is copied, not shared. Change one, change both.

Why it exists: the storage venture ran out of new products and reposted
identical pins from 2026-10-12, because nothing stopped it. HappyPet's
per-slug .fired sentinel stops an ordinary re-run but not --force, and knows
nothing about images. This ledger is the one record of what each post has
already gone out with, so a pin pipeline can refuse a repeat and still allow
the same post with a fresh image.

SHAPE: {post: {image_key: "YYYY-MM-DD"}}. The date is for a human reading the
file; the guard only asks whether the pair is present.

IMAGE IDENTITY is the URL, normalized: surrounding whitespace, the scheme, any
port, the query string and the fragment are ignored, the host is lowercased,
and the path is percent-decoded. So "https://Host/a.png?v=2" and
"http://host/a.png" are the same image. The path keeps its case, because
image hosts treat paths as case-sensitive. NOT detected: the same picture
uploaded again under a different path. A fresh image is expected to arrive
under a new path, which is what makes it count as new.

FAILURE POSTURE: data, not instrumentation. A missing file reads as empty. A
file that exists but will not parse, or has the wrong shape, RAISES: an
unreadable ledger silently treated as empty would let every past pin repeat.
"""
import json
import os
import tempfile
from urllib.parse import unquote, urlsplit

# Same number in both pipelines: 6 means "refused, this pin already went out".
REPEAT_REFUSED_EXIT_CODE = 6


def post_key(post):
    """The ledger key for a post (slug or ASIN), or None if it has none."""
    if not isinstance(post, str):
        return None
    return post.strip() or None


def image_key(url):
    """The normalized identity of an image URL, or None if it has none."""
    if not isinstance(url, str) or not url.strip():
        return None
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if not host and not path:
        return None
    return host + path


def load(path) -> dict:
    """The ledger at `path`. Empty if the file does not exist; raises if it is unusable."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        ledger = json.load(f)
    if not isinstance(ledger, dict):
        raise ValueError(f"{path} does not hold an object of post -> images")
    for post, images in ledger.items():
        if post_key(post) is None or not isinstance(images, dict):
            raise ValueError(f"{path} entry {post!r} is not post -> {{image: date}}")
        for image, date in images.items():
            if not isinstance(image, str) or not image or not isinstance(date, str):
                raise ValueError(f"{path} entry {post!r} has an unusable image record")
    return ledger


def already_pinned(ledger: dict, post, image_url) -> bool:
    """True if this post has already been pinned with this image."""
    key, image = post_key(post), image_key(image_url)
    if key is None or image is None:
        return False
    return image in (ledger or {}).get(key, {})


def add(ledger: dict, post, image_url, date: str) -> dict:
    """Add one pair to an in-memory ledger and return it. Raises on an unusable pair."""
    key, image = post_key(post), image_key(image_url)
    if key is None or image is None:
        raise ValueError(f"cannot record a pin without a post and an image ({post!r}, {image_url!r})")
    ledger.setdefault(key, {})[image] = date
    return ledger


def save(path, ledger: dict) -> None:
    """Write the ledger atomically: an interrupted write leaves the old file or the new one."""
    directory = os.path.dirname(os.fspath(path)) or "."
    handle, temp_path = tempfile.mkstemp(dir=directory, prefix=".pin_ledger-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(temp_path, path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def record(path, post, image_url, date: str) -> None:
    """Remember that `post` was pinned with `image_url` on `date`. Raises on failure."""
    save(path, add(load(path), post, image_url, date))
