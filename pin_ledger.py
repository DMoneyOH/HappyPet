"""No-repeat ledger: which (post, image) pairs have already been pinned, and where.

MIRRORED FILE. An identical copy serves a second pin pipeline in a separate
repository, which keys its posts by product ID rather than slug. Neither can
import the other, so the module is copied. Change both copies together; each
side's tests pin this file's SHA-256 so a one-sided edit fails.

What it is for: a pin pipeline that re-runs, is forced, or runs out of fresh
posts must not put the same image for the same post on a board twice. The
ledger records every image each post has gone out with, so a caller can refuse
a repeat and still allow the same post with a new image.

SHAPE: {post: {image_key: {board: "YYYY-MM-DD"}}}. ANY_BOARD ("*") as a board
means "already out, boards unrecorded" and protects every board; it is how
history written before this ledger existed is seeded. The dates are for a
human reading the file.

IMAGE IDENTITY is the URL, normalized: surrounding whitespace, the scheme, any
port, the query string and the fragment are ignored, the host is lowercased,
and percent-encoding in the path is made canonical. So "https://Host/a.png?v=2",
"http://host/a.png" and "https://host/%61.png" are the same image. The path
keeps its case, because image hosts treat paths as case-sensitive.

What follows from keying on the URL:
  * A refreshed image MUST arrive under a new path. An image regenerated in
    place, at the same URL, is still the same image here and is refused.
  * The same picture uploaded again under a different path is NOT detected.

WRITE-AHEAD, AND ITS ONE RISK. A caller should record a board BEFORE firing
it and remove the record if the fire fails, so a live pin is never missing
from the ledger. The reverse risk remains: if the process dies between the
record and the fire, that board stays recorded but was never pinned, and the
guard refuses it from then on. Recovery is a hand edit of the ledger file or
a new image path.

FAILURE POSTURE: data, not instrumentation. A missing file reads as empty; a
caller that knows pins went out before should refuse to run without one. A file
that will not parse, has the wrong shape, or holds keys that are not in
normalized form RAISES: a ledger silently read as empty, or one whose keys no
lookup can match, would let every past pin repeat.
"""
import json
import os
import tempfile
from urllib.parse import quote, unquote, urlsplit

# The same number in every pipeline that uses this module: 6 means "refused,
# this pin already went out".
REPEAT_REFUSED_EXIT_CODE = 6

ANY_BOARD = "*"

# Characters left unescaped in a canonical path: RFC 3986 unreserved and
# sub-delims, plus "/", ":" and "@". quote(unquote(path)) with a fixed set is
# idempotent, so a stored key normalizes to itself.
_PATH_SAFE = "/:@!$&'()*+,;=-._~"


def post_key(post):
    """The ledger key for a post (slug or product ID), or None if it has none."""
    if not isinstance(post, str):
        return None
    return post.strip() or None


def image_key(url):
    """The normalized identity of an image URL, or None if it has none or will not parse."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError:  # e.g. an unbalanced IPv6 bracket
        return None
    path = quote(unquote(parts.path), safe=_PATH_SAFE)
    if not host and not path:
        return None
    return host + path


def _is_normalized_image_key(key) -> bool:
    # A stored key has no scheme; "//" puts its host back where urlsplit looks.
    return isinstance(key, str) and image_key("//" + key) == key


def load(path) -> dict:
    """The ledger at `path`. Empty if the file does not exist; raises if it is unusable."""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        ledger = json.load(f)
    if not isinstance(ledger, dict):
        raise ValueError(f"{path} does not hold an object of post -> images")
    for post, images in ledger.items():
        if post_key(post) != post or not isinstance(images, dict):
            raise ValueError(f"{path}: post {post!r} is not a normalized key with images")
        for image, boards in images.items():
            if not _is_normalized_image_key(image) or not isinstance(boards, dict) or not boards:
                raise ValueError(f"{path}: {post!r} image {image!r} is not a normalized key with boards")
            for board, date in boards.items():
                if post_key(board) != board or not isinstance(date, str):
                    raise ValueError(f"{path}: {post!r} {image!r} has an unusable board record")
    return ledger


def already_pinned(ledger: dict, post, image_url, board=None) -> bool:
    """True if this post has gone out with this image: on `board` if one is
    given, on any board if not. ANY_BOARD in the record counts for every board."""
    key, image = post_key(post), image_key(image_url)
    if key is None or image is None:
        return False
    boards = (ledger or {}).get(key, {}).get(image, {})
    if board is None:
        return bool(boards)
    return board in boards or ANY_BOARD in boards


def add(ledger: dict, post, image_url, board, date: str) -> dict:
    """Add one (post, image, board) record to an in-memory ledger and return it.
    Raises on a record with no usable post, image or board."""
    key, image, where = post_key(post), image_key(image_url), post_key(board)
    if key is None or image is None or where is None:
        raise ValueError(f"cannot record a pin without a post, an image and a board "
                         f"({post!r}, {image_url!r}, {board!r})")
    # The same test load() applies: a key that does not survive the round trip
    # (a scheme-less URL whose first segment is capitalized, an IPv6 host)
    # would write a ledger that every later load refuses.
    if not _is_normalized_image_key(image):
        raise ValueError(f"cannot record {image_url!r}: its key {image!r} would not load back")
    ledger.setdefault(key, {}).setdefault(image, {})[where] = date
    return ledger


def discard(ledger: dict, post, image_url, board) -> dict:
    """Remove one board record from an in-memory ledger, pruning what it empties."""
    key, image, where = post_key(post), image_key(image_url), post_key(board)
    images = ledger.get(key, {})
    images.get(image, {}).pop(where, None)
    if image in images and not images[image]:
        del images[image]
    if key in ledger and not ledger[key]:
        del ledger[key]
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


def record(path, post, image_url, board, date: str) -> None:
    """Remember that `post` went out with `image_url` on `board` on `date`. Raises on failure."""
    save(path, add(load(path), post, image_url, board, date))
