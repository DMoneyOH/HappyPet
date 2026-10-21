"""Tell IndexNow (Bing, Yandex and the other engines that share it) which
pages changed, so they recrawl them without waiting.

    python3 indexnow.py changed BASE HEAD [--dry-run]   # pages a push changed
    python3 indexnow.py sitemap [--dry-run]             # every URL in sitemap.xml

deploy.yml runs `changed` after the Pages deploy succeeds; indexnow.yml runs
`sitemap` by hand. A rejected or failed submit is logged and never fails the
job: IndexNow is a hint, and the site is already live.

The key is the root file `<key>.txt`, whose content is its own name. Jekyll
serves it at /<key>.txt, which is how IndexNow checks the site owns the key.
Stdlib only, so the workflow step needs no pip install."""
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent
SITE = "https://happypetproductreviews.com"
HOST = "happypetproductreviews.com"
ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10000  # IndexNow's per-request limit

_KEY_FILE = re.compile(r"^[0-9a-f]{32}\.txt$")
_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)
# Jekyll post filename: YYYY-MM-DD-<title>.md; <title> is the :title in the permalink.
_POST = re.compile(r"^_posts/\d{4}-\d{2}-\d{2}-(.+)\.md$")
# Pages that publish under their own URL: root .md/.html files and the topic hubs.
_PAGE = re.compile(r"^(?:[^/]+\.(?:md|html)|hubs/[^/]+\.md)$")


def find_key() -> str:
    """The one root `<32 hex>.txt` file whose content is its own name."""
    keys = [p.stem for p in REPO.iterdir()
            if _KEY_FILE.match(p.name) and p.read_text(encoding="utf-8").strip() == p.stem]
    if len(keys) != 1:
        raise SystemExit(f"indexnow: expected one key file at the repo root, found {len(keys)}")
    return keys[0]


def _field(front_matter: str, name: str):
    m = re.search(rf"^{name}:[ \t]*(.*?)[ \t]*$", front_matter, re.M)
    return m.group(1).strip("'\"") if m else None


def url_for(path: str, text: str):
    """The live URL Jekyll publishes `path` (repo-relative, forward slashes) at,
    given the file's content, or None if it publishes no page.

    Posts follow _config.yml's `permalink: /:categories/:title/`: Jekyll 3.10
    lowercases each category and joins them with '/', and :title is the
    filename after the date. A page needs its own `permalink:` (index.md is
    '/'); a page without one is skipped rather than guessed at."""
    fm = _FRONT_MATTER.match(text)
    if not fm:
        return None
    fm = fm.group(1)
    if _field(fm, "published") == "false" or _field(fm, "sitemap") == "false":
        return None
    permalink = _field(fm, "permalink")
    post = _POST.match(path)
    if post:
        if not permalink:
            raw = _field(fm, "categories") or ""
            cats = [c.strip().strip("'\"").lower() for c in raw.strip("[]").split(",")]
            permalink = "/" + "/".join([c for c in cats if c] + [post.group(1)]) + "/"
    elif _PAGE.match(path):
        if not permalink and path == "index.md":
            permalink = "/"
    else:
        return None
    return SITE + permalink if permalink and permalink.startswith("/") else None


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def changed_urls(base: str, head: str) -> list:
    """URLs of the posts and pages changed between two commits. A deleted or
    renamed-away file is read at `base`, so its old URL is submitted too and
    the engines learn it moved or is gone."""
    diff = _git("diff", "--name-status", "-M", base, head)
    urls = []
    for line in diff.splitlines():
        status, *paths = line.split("\t")
        if status.startswith("D"):
            old, new = paths[0], None
        elif status.startswith("R"):
            old, new = paths
        else:
            old, new = None, paths[-1]
        for rev, path in ((base, old), (head, new)):
            if path and (_POST.match(path) or _PAGE.match(path)):
                url = url_for(path, _git("show", f"{rev}:{path}"))
                if url and url not in urls:
                    urls.append(url)
    return urls


def sitemap_urls(xml: str) -> list:
    """Every <loc> on this site in a sitemap.xml body, in order, deduplicated."""
    urls = []
    for loc in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml):
        if loc.startswith(SITE + "/") and loc not in urls:
            urls.append(loc)
    return urls


def fetch_sitemap(opener=urllib.request.urlopen) -> list:
    """Every URL in the live sitemap.xml. Raises if it cannot be read."""
    with opener(f"{SITE}/sitemap.xml", timeout=30) as resp:
        return sitemap_urls(resp.read().decode("utf-8"))


def build_payload(key: str, urls: list) -> dict:
    return {"host": HOST, "key": key, "keyLocation": f"{SITE}/{key}.txt",
            "urlList": urls[:MAX_URLS]}


def submit(payload: dict, opener=urllib.request.urlopen) -> bool:
    """POST the payload once. 200 and 202 are success; anything else, or no
    answer at all, is logged and returns False. Never raises."""
    req = urllib.request.Request(
        ENDPOINT, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with opener(req, timeout=30) as resp:
            status, body = resp.status, resp.read(500)
    except urllib.error.HTTPError as e:
        status, body = e.code, e.read(500)
    except Exception as e:  # network failure: log it, never fail the deploy
        print(f"indexnow: submit failed, no response: {e}")
        return False
    ok = status in (200, 202)
    print(f"indexnow: HTTP {status} ({'accepted' if ok else 'not accepted'}) for "
          f"{len(payload['urlList'])} URL(s)"
          + ("" if ok else f": {body.decode('utf-8', 'replace')}"))
    return ok


def main(argv) -> int:
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if args[:1] == ["changed"] and len(args) == 3:
        base, head = args[1], args[2]
        if not base.strip("0"):
            print("indexnow: no base commit (first push of a branch) -- nothing to diff")
            return 0
        try:
            urls = changed_urls(base, head)
        except subprocess.CalledProcessError as e:
            print(f"indexnow: git could not diff {base}..{head}: {e.stderr.strip()}")
            return 0
    elif args == ["sitemap"]:
        try:
            urls = fetch_sitemap()
        except Exception as e:
            print(f"indexnow: could not read the live sitemap: {e}")
            return 0
    else:
        print(__doc__)
        return 2
    if not urls:
        print("indexnow: no published page changed -- nothing to submit")
        return 0
    payload = build_payload(find_key(), urls)
    print(f"indexnow: {len(payload['urlList'])} URL(s):")
    for u in payload["urlList"]:
        print(f"  {u}")
    if dry_run:
        print("indexnow: dry run -- nothing sent")
        return 0
    submit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
