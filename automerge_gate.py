#!/usr/bin/env python3
"""Auto-merge gate for routine Stage-1 content PRs and refill/* PRs (run by
.github/workflows/automerge.yml).

A PR is merged only when EVERY condition holds; anything else is left for a human
(exit 0, reason printed). The decision logic is `evaluate()`, a pure function so the
case table in test_pipeline.py can attack it without a network.

Conditions (all required):
  1. PR is open, not a draft, same-repo, based on main, not labelled `no-automerge`
     (label compared case-folded, with `-`/`_`/space treated alike).
  2. Opened by the Claude GitHub App: issue `performed_via_github_app.slug == "claude"`
     AND user.login in ALLOWED_AUTHORS. (The PR *author* alone cannot tell the routine
     from the Director's own `gh` PRs -- both show up as DMoneyOH; only the app
     attribution differs. GitHub sets it from the token used, so it cannot be spoofed
     from the PR text, branch name or commit author.)
  3. The named `pytest` check (app github-actions) succeeded on the exact head SHA the
     triggering CI run tested, and no other check on that SHA is pending or failing.
     Zero matching checks is a hold, never a pass. A malformed check entry is a hold.
  4. Every changed path -- and every rename source -- fully matches ALLOWED_PATHS, and
     every file entry is well formed. The draft, its pin image and its pin JSON may only
     be ADDED (never modified/removed/renamed); products.json may be added or modified.
  5. Every touched file is a plain regular file (git mode 100644) at the head SHA -- no
     symlink, executable or submodule (one recursive tree read).
  6. products.json is PARSED (not substring-searched): no decoded key or value, at any
     depth, may contain NEEDS_ASIN/NEEDS_IMAGE (a held placeholder still burns a
     publish slot), and no value starting `REVIEW` may be newly introduced (per topic + field)
     relative to the base (main already carries one legitimate REVIEW: chewy_url).
     Unparsable = hold.
  7. Each added `_pin_queue/<slug>.json` is fetched at the head SHA and validated by
     `pin_problems()`. Its `article_url` is echoed by publish.yml into $GITHUB_OUTPUT and
     both URLs reach curl in pin.yml, so a newline/quote/`$` there is shell/output
     injection. The validator is built from the real historical pin files (all 38 in
     _pin_queue/sent/ pass it; a test pins that): strict https URLs on the site host,
     known keys only, printable text, size cap.

After a successful merge, `publish.yml` is dispatched, but only when the PR ADDED a
_posts/DRAFT-*.md file. A merge made with GITHUB_TOKEN does not fire publish.yml's
push trigger, and a products.json-only merge must not publish a leftover draft early.

Known limits: the gate trusts the GitHub API's file list (a PR with >3000 changed files
is truncated by the API, but MAX_FILES=30 already holds anything that large), and the
draft's markdown BODY is not inspected -- it is what Stage 1 writes and what CI's
content-integrity tests judge.

A PR whose head branch starts with `refill/` is judged by the separate refill rule instead
(`evaluate_refill`; its conditions open the "refill rule" section below). It never falls back
to the rule above.

Env: GH_TOKEN, REPO (owner/name), RUN_HEAD_SHA, PR_NUMBERS (JSON list), AUTOMERGE_ENABLED.
`--dry-run` evaluates and prints; it never merges or dispatches. So does any run where
AUTOMERGE_ENABLED is not exactly "true".
"""
import json
import os
import re
import subprocess
import sys
import unicodedata
from collections import Counter
from pathlib import PurePosixPath
from typing import NamedTuple

ALLOWED_AUTHORS = frozenset({"DMoneyOH"})
ALLOWED_APP_SLUGS = frozenset({"claude"})
BASE_BRANCH = "main"
REQUIRED_CHECK = "pytest"
CHECK_APP_SLUG = "github-actions"
OPT_OUT_LABEL = "no-automerge"
MAX_FILES = 30
REGULAR_FILE_MODE = "100644"

_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# What routine Stage-1 PRs actually touch (PRs #85, #105, #111): the draft, its pin
# spec, its pin image. Top-level only -- _pin_queue/sent/ and _pin_queue/.fired/ are
# pipeline state, not content. products.json is the refill/queue file. Exact
# full-match patterns, never prefixes: `_posts/DRAFT-x.md.bak`, `_posts/sub/DRAFT-x.md`
# and `_pin_queue/../x` all fail.
_DRAFT = re.compile(rf"_posts/DRAFT-{_SLUG}\.md")
_PIN_JSON = re.compile(rf"_pin_queue/{_SLUG}\.json")
_PIN_IMAGE = re.compile(rf"assets/images/pins/{_SLUG}\.jpg")
_PRODUCTS = re.compile(r"products\.json")
ALLOWED_PATHS = (_DRAFT, _PIN_JSON, _PIN_IMAGE, _PRODUCTS)
PLACEHOLDER_MARKERS = ("NEEDS_ASIN", "NEEDS_IMAGE")
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})

# ---- pin JSON shape, from the 38 real files in _pin_queue/sent/ ------------------
PIN_MAX_BYTES = 4096            # largest real file is 645
PIN_REQUIRED = ("title", "article_url", "slug")
PIN_KEYS = frozenset(PIN_REQUIRED + ("description", "image_url", "species", "topical_sheet"))
_SITE = r"https://happypetproductreviews\.com"
# Real article_url: https://<host>/<category>/<slug>/?utm_source=pinterest&utm_medium=social&utm_campaign=pin
_ARTICLE_URL = re.compile(
    rf"{_SITE}/{_SLUG}/{_SLUG}/(?:\?utm_source=pinterest&utm_medium=social&utm_campaign=pin)?")
# Real image_url: the site's own pin jpg (optional ?v=YYYYMMDD cache-bust); one old file
# points at the Amazon CDN, which is kept valid so a legitimate historical shape still passes.
_IMAGE_URL = re.compile(
    rf"(?:{_SITE}/assets/images/pins/{_SLUG}\.jpg"
    r"|https://m\.media-amazon\.com/images/I/[A-Za-z0-9._-]+\.jpg)(?:\?v=[0-9]{8})?")
_SHEET = re.compile(r"HAPPYPET_SHEET_ID_[A-Z]+")
_SPECIES = frozenset({"dog", "cat", "both"})
# Free-text fields flow to Sheets/IFTTT via Python, not shell. Real text contains $ ' | and
# non-ASCII, so those stay legal; control chars, quotes, backticks, backslashes and angle
# brackets do not appear in any real file and are refused. A leading = + - @ would be a
# spreadsheet formula.
_TEXT_FORBIDDEN = frozenset('"`\\<>')
_TEXT_MAX = {"title": 300, "description": 1000}
_FORMULA_LEADERS = "=+-@"


class Verdict(NamedTuple):
    ok: bool
    reasons: list
    publish: bool


def path_allowed(path) -> bool:
    return isinstance(path, str) and any(p.fullmatch(path) for p in ALLOWED_PATHS)


# U+2010-U+2015 (hyphen .. horizontal bar) and U+2212 (minus) all read as a dash to a human.
_DASHES = dict.fromkeys([*range(0x2010, 0x2016), 0x2212], "-")


def _label_key(name) -> str:
    """Compare-key for a label: NFKC, invisible format characters (category Cf: zero-width
    space/joiner, BOM, soft hyphen, ...) dropped, Unicode dashes -> '-', then case-folded
    with runs of space/_/- collapsed to one '-'."""
    text = unicodedata.normalize("NFKC", str(name))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf").translate(_DASHES)
    return re.sub(r"[\s_-]+", "-", text.strip().casefold())


def _text_problem(key: str, value) -> str | None:
    if not isinstance(value, str) or not value:
        return f"{key} must be a non-empty string"
    if len(value) > _TEXT_MAX[key]:
        return f"{key} is too long"
    if not value.isprintable():
        return f"{key} has control or non-printable characters"
    if _TEXT_FORBIDDEN & set(value):
        return f"{key} has a quote, backtick, backslash or angle bracket"
    if value[0] in _FORMULA_LEADERS or value != value.strip():
        return f"{key} starts with a formula character or has edge whitespace"
    return None


def pin_problems(path: str, text) -> list:
    """Problems with one added _pin_queue/<slug>.json; empty list = safe to merge."""
    if not isinstance(text, str):
        return [f"{path}: content unavailable"]
    if len(text.encode("utf-8", "replace")) > PIN_MAX_BYTES:
        return [f"{path}: larger than {PIN_MAX_BYTES} bytes"]
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        return [f"{path}: not valid JSON"]
    if not isinstance(data, dict):
        return [f"{path}: JSON is not an object"]
    bad = []
    unknown = set(data) - PIN_KEYS
    if unknown:
        bad.append("unknown key(s) " + ", ".join(sorted(map(ascii, unknown))))
    missing = [k for k in PIN_REQUIRED if k not in data]
    if missing:
        bad.append("missing key(s) " + ", ".join(missing))
    for key in ("title", "description"):
        if key in data and (p := _text_problem(key, data[key])):
            bad.append(p)
    for key, rx in (("article_url", _ARTICLE_URL), ("image_url", _IMAGE_URL)):
        if key in data and not (isinstance(data[key], str) and rx.fullmatch(data[key])):
            bad.append(f"{key} does not match the strict URL pattern")
    slug = data.get("slug")
    if "slug" in data and not (isinstance(slug, str) and re.fullmatch(_SLUG, slug)
                               and slug == PurePosixPath(path).stem):
        bad.append("slug is not a slug matching the file name")
    if "species" in data and data["species"] not in _SPECIES:
        bad.append("species is not dog/cat/both")
    if "topical_sheet" in data and data["topical_sheet"] is not None and not (
            isinstance(data["topical_sheet"], str) and _SHEET.fullmatch(data["topical_sheet"])):
        bad.append("topical_sheet is not a HAPPYPET_SHEET_ID_* name or null")
    return [f"{path}: {b}" for b in bad]


def _strings(node, depth=0):
    """Every decoded string in a JSON tree: dict keys, dict values, list items."""
    if depth > 50:
        raise ValueError("JSON nested too deeply")
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _strings(v, depth + 1)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v, depth + 1)


def _is_review_sentinel(s: str) -> bool:
    # Same test the pipeline applies (generate_posts.py, validate_published_chewy_links.py):
    # ANY value starting with REVIEW is a sentinel, not only REVIEW / REVIEW:<url>.
    return s.startswith("REVIEW")


def _review_sites(node, topic=None, path=(), depth=0, out=None) -> Counter:
    """Where every REVIEW sentinel sits, as a multiset of (topic, field-path). The value is
    not part of the key (a sentinel re-pointed at another URL is not a new sentinel), and
    list positions are not either (re-ordering entries is not a move). `topic` is the
    `topic` of the top-level entry the sentinel lives in, so a sentinel moved to another
    topic, or carried by a new topic, is a different site."""
    if out is None:
        out = Counter()
    if depth > 50:
        raise ValueError("JSON nested too deeply")
    if isinstance(node, str):
        if _is_review_sentinel(node):
            out[(topic, path)] += 1
    elif isinstance(node, dict):
        for k, v in node.items():
            if _is_review_sentinel(k):
                out[(topic, path + ("<key>",))] += 1
            _review_sites(v, topic, path + (k,), depth + 1, out)
    elif isinstance(node, list):
        for v in node:
            t = v.get("topic") if depth == 0 and isinstance(v, dict) else topic
            _review_sites(v, t if isinstance(t, str) else None, path + ("[]",), depth + 1, out)
    return out


def products_problems(head_text, base_text) -> list:
    """Parse products.json (never substring-search the raw text: JSON escapes such as
    \\u004eEEDS_ASIN decode to the sentinel but do not match a raw search)."""
    if not isinstance(head_text, str):
        return ["products.json changed but its head content was not available"]
    try:
        head_json = json.loads(head_text)
        head = list(_strings(head_json))
        head_sites = _review_sites(head_json)
    except (ValueError, RecursionError):
        return ["products.json is not parseable JSON"]
    bad = []
    if any(m in s for s in head for m in PLACEHOLDER_MARKERS):
        bad.append("products.json still carries NEEDS_ASIN/NEEDS_IMAGE placeholders")
    if head_sites:
        try:
            base_sites = _review_sites(json.loads(base_text)) if isinstance(base_text, str) else None
        except (ValueError, RecursionError):
            base_sites = None
        if base_sites is None:
            bad.append("products.json has REVIEW sentinel(s) and the base is unreadable")
        elif head_sites - base_sites:      # Counter difference: only sites beyond the base's
            bad.append("products.json introduces a new REVIEW sentinel")
    return bad


def _check_problems(check_runs: list | None) -> list:
    if not isinstance(check_runs, list):
        return ["check runs could not be read completely"]
    why = []
    runs = []
    for c in check_runs:
        if isinstance(c, dict):
            runs.append(c)
        else:
            why.append("malformed check entry")
    named = [c for c in runs if c.get("name") == REQUIRED_CHECK
             and (c.get("app") or {}).get("slug") == CHECK_APP_SLUG]
    if not named:
        why.append(f"no {REQUIRED_CHECK!r} check from {CHECK_APP_SLUG} on the head SHA")
    elif not all(c.get("status") == "completed" and c.get("conclusion") == "success"
                 for c in named):
        why.append(f"{REQUIRED_CHECK!r} check is not green")
    for c in runs:
        if c.get("status") != "completed" or c.get("conclusion") not in _OK_CONCLUSIONS:
            why.append(f"check {c.get('name')!r} is not green "
                       f"({c.get('status')}/{c.get('conclusion')})")
    return why


def _file_problems(files: list, modes) -> list:
    why = []
    for f in files:
        if not isinstance(f, dict) or not isinstance(f.get("filename"), str) \
                or not f["filename"]:
            why.append("malformed file entry")
            continue
        name, prev, status = f["filename"], f.get("previous_filename"), f.get("status")
        for p in (name, prev):
            if p is not None and not path_allowed(p):
                why.append(f"path not on allowlist: {p}")
        # Draft / pin image / pin JSON are only ever created by a routine PR.
        if (_DRAFT.fullmatch(name) or _PIN_JSON.fullmatch(name)
                or _PIN_IMAGE.fullmatch(name)) and status != "added":
            why.append(f"{name} is {status!r}; only 'added' is auto-mergeable")
        elif _PRODUCTS.fullmatch(name) and status not in ("added", "modified"):
            why.append(f"products.json is {status!r}; only added/modified is auto-mergeable")
        mode = (modes or {}).get(name)     # a removal is held above, so every file is at head
        if mode != REGULAR_FILE_MODE:
            why.append(f"{name} is not a regular file at head (mode {mode!r})")
    return why


def _pr_problems(pr: dict, run_sha: str, repo: str) -> list:
    """The checks both rules open with: open, not a draft, based on main, same repo, head SHA
    is the SHA the CI run tested, no opt-out label."""
    why = []
    if pr.get("state") != "open" or pr.get("merged"):
        why.append("PR is not open")
    if pr.get("draft"):
        why.append("PR is a draft")
    if (pr.get("base") or {}).get("ref") != BASE_BRANCH:
        why.append(f"base is not {BASE_BRANCH}")
    head = pr.get("head") or {}
    if ((head.get("repo") or {}).get("full_name")) != repo:
        why.append("head is not in this repository (fork)")
    head_sha = head.get("sha")
    if not head_sha or head_sha != run_sha:
        why.append("head SHA is not the SHA the CI run tested (PR moved after CI)")
    if any(_label_key((lb or {}).get("name") if isinstance(lb, dict) else lb) == OPT_OUT_LABEL
           for lb in pr.get("labels") or []):
        why.append(f"labelled {OPT_OUT_LABEL}")
    return why


def evaluate(pr: dict, issue: dict, files: list, check_runs: list,
             run_sha: str, repo: str, products_text: str | None = None,
             base_products_text: str | None = None, pin_texts: dict | None = None,
             modes: dict | None = None) -> Verdict:
    """Pure decision. `pr`=REST pulls/{n}, `issue`=REST issues/{n}, `files`=REST
    pulls/{n}/files entries, `check_runs`=check_runs of the PR head SHA, `modes`=
    {path: git mode} at the head SHA, `pin_texts`={path: head content} for each pin JSON."""
    why = _pr_problems(pr, run_sha, repo)

    author = (issue.get("user") or {}).get("login")
    app = (issue.get("performed_via_github_app") or {}).get("slug")
    if author not in ALLOWED_AUTHORS:
        why.append(f"author {author!r} not allowlisted")
    if app not in ALLOWED_APP_SLUGS:
        why.append(f"not opened via the Claude GitHub App (app={app!r})")

    why += _check_problems(check_runs)

    if not files:
        why.append("empty file list")
    elif len(files) > MAX_FILES:
        why.append(f"more than {MAX_FILES} changed files")
    why += _file_problems(files, modes)

    good = [f for f in files if isinstance(f, dict) and isinstance(f.get("filename"), str)]
    if any(f["filename"] == "products.json" or f.get("previous_filename") == "products.json"
           for f in good):
        why += products_problems(products_text, base_products_text)
    for f in good:
        if _PIN_JSON.fullmatch(f["filename"]) and f.get("status") == "added":
            why += pin_problems(f["filename"], (pin_texts or {}).get(f["filename"]))

    publish = any(f.get("status") == "added" and _DRAFT.fullmatch(f["filename"])
                  for f in good)
    return Verdict(not why, why, publish)


# ------------------------------------------------------------------ refill rule
# A separate rule for `refill/*` PRs: build step 2 of the refill automation design spec
# (docs/superpowers/specs/2026-09-25-refill-automation-design.md on branch
# docs/refill-automation-spec, PR #120, not yet on main). Refill PRs come from the local
# refill session, which pushes and opens them under the Director's own gh login, so they
# never carry the Claude App attribution the routine rule above requires. Instead of who
# opened them, this rule judges what they change: products.json only, and only by
# appending canonical entries or filling placeholders in place.
#
# Merges only when ALL hold:
#   1. head branch fully matches REFILL_BRANCH; PR open, not draft, based on main, no
#      opt-out label, head SHA is the SHA CI tested;
#   2. same repo (not a fork);
#   3. the ONLY changed file is products.json, status `modified`, no rename source, a
#      regular file at head;
#   4. the `pytest` check is green on the head SHA and no other check is pending or red;
#   5. products.json, parsed: no decoded key or value anywhere in the head file contains
#      NEEDS_; head is the merge-base list in the same order, every non-placeholder entry
#      unchanged (compared with JSON types, so 1, 1.0 and true differ), followed only by
#      new entries at the end. A base NEEDS_ASIN/NEEDS_IMAGE placeholder may be filled in
#      place: same position, same topic. At least one entry must be new or filled, and each
#      such entry must carry no REVIEW sentinel or `REVIEW:` anywhere in its strings, an
#      ASIN no other head entry uses, the canonical affiliate link for its ASIN (exact,
#      case-sensitive tag), an m.media-amazon.com image and a null or chewy.sjv.io
#      chewy_url.
# The diff is judged against the MERGE BASE, not the base tip, so a main-side edit made
# during the refill window does not read as the PR changing an existing entry.

REFILL_BRANCH = re.compile(rf"refill/{_SLUG}")
AFFILIATE_TAG = "happypetdc-20"      # literal: refill_products.py lets an env var override it
_ASIN = re.compile(r"B0[A-Z0-9]{8}")
# Product photo for a new products.json entry. Stricter than the pin rule's _IMAGE_URL on
# purpose: that one validates pin JSON written by Stage 1 (site pin jpg, or one historical
# Amazon CDN jpg, with an optional ?v= cache-bust); this one validates what manual_resolve.py
# accepts (refill_products.IMAGE_HOST_RE: jpg/jpeg/png/webp on m.media-amazon.com, no
# query), with fullmatch and a closed character set instead of [^\s"'].
_AMAZON_IMAGE = re.compile(
    r"https://m\.media-amazon\.com/images/I/[A-Za-z0-9._+-]+\.(?:jpg|jpeg|png|webp)")
# Impact tracking link to Chewy. 32975 is CHEWY_CAMPAIGN_ID and APIG_24727 carries
# CHEWY_CATALOG_ID 24727 (both chewy_lookup.py defaults). 7160344 and 3054490 are not named
# anywhere in this repo: they are the fixed segments Impact's catalog API returns, identical
# in all 35 distinct chewy_url values in products.json history. The `u` value may hold only
# URL-encoded characters, so it cannot add a second query parameter.
_CHEWY_URL = re.compile(
    r"https://chewy\.sjv\.io/c/7160344/3054490/32975\?prodsku=[0-9]+"
    r"&u=https%3A%2F%2Fwww\.chewy\.com%2F[A-Za-z0-9%._~-]+(?:&intsrc=APIG_[0-9]+)?")


def is_refill_branch(ref) -> bool:
    return isinstance(ref, str) and REFILL_BRANCH.fullmatch(ref) is not None


def _is_placeholder(entry: dict) -> bool:
    return entry.get("asin") == "NEEDS_ASIN" or entry.get("image") == "NEEDS_IMAGE"


def _reject_deep_nesting(node) -> None:
    """Raise ValueError if `node` nests deeper than _strings() allows."""
    for _ in _strings(node):
        pass


def _entries(text, label: str):
    """The products.json entries in file order, or (None, reason) when the file is
    unavailable or is not a list of objects with unique slug topics."""
    if not isinstance(text, str):
        return None, f"{label} products.json is not available"
    try:
        data = json.loads(text)
        _reject_deep_nesting(data)
    except (ValueError, RecursionError):
        return None, f"{label} products.json is not parseable JSON"
    if not isinstance(data, list):
        return None, f"{label} products.json is not a list"
    topics = set()
    for e in data:
        topic = e.get("topic") if isinstance(e, dict) else None
        if not (isinstance(topic, str) and re.fullmatch(_SLUG, topic)):
            return None, f"{label} products.json has an entry without a slug topic"
        if topic in topics:
            return None, f"{label} products.json has duplicate topic {topic!r}"
        topics.add(topic)
    return data, None


def _canonical(entry) -> str:
    """Type-aware identity for an entry: 1, 1.0 and true dump differently; key order does
    not matter."""
    return json.dumps(entry, sort_keys=True)


def _has_review_marker(s: str) -> bool:
    return _is_review_sentinel(s) or "REVIEW:" in s


def refill_entry_problems(entry: dict) -> list:
    """Problems with one new or filled entry; empty list = canonical."""
    topic = entry["topic"]
    bad = []
    if any(_has_review_marker(s) for s in _strings(entry)):
        bad.append("carries a REVIEW marker")
    asin = entry.get("asin")
    if not (isinstance(asin, str) and _ASIN.fullmatch(asin)):
        bad.append("asin is not a B0 ASIN")
    elif entry.get("affiliate_url") != f"https://www.amazon.com/dp/{asin}?tag={AFFILIATE_TAG}":
        bad.append("affiliate_url is not the canonical link for its ASIN")
    image = entry.get("image")
    if not (isinstance(image, str) and _AMAZON_IMAGE.fullmatch(image)):
        bad.append("image is not an m.media-amazon.com product photo")
    if "chewy_url" not in entry:
        bad.append("chewy_url is missing")
    elif entry["chewy_url"] is not None and not (
            isinstance(entry["chewy_url"], str) and _CHEWY_URL.fullmatch(entry["chewy_url"])):
        bad.append("chewy_url is neither null nor a chewy.sjv.io link")
    return [f"entry {topic!r}: {b}" for b in bad]


def refill_products_problems(head_text, merge_base_text) -> list:
    head, why = _entries(head_text, "head")
    if why:
        return [why]
    base, why = _entries(merge_base_text, "merge-base")
    if why:
        return [why]
    if any("NEEDS_" in s for s in _strings(head)):
        return ["products.json carries a NEEDS_ marker"]
    if len(head) < len(base):
        return ["products.json removes an existing entry"]
    bad = []
    fresh = []
    # Head must be the merge base, in order, with base placeholders filled in place, followed
    # only by new entries.
    for i, old in enumerate(base):
        if head[i]["topic"] != old["topic"]:
            bad.append(f"existing entry {old['topic']!r} is not at position {i} "
                       "(moved, removed or displaced by an insert)")
        elif _is_placeholder(old):
            fresh.append(head[i])
        elif _canonical(head[i]) != _canonical(old):
            bad.append(f"existing entry {old['topic']!r} was changed")
    fresh += head[len(base):]
    if not fresh:
        bad.append("products.json adds no entry")
    asins = Counter(e.get("asin") for e in head if isinstance(e.get("asin"), str))
    for e in fresh:
        bad += refill_entry_problems(e)
        if asins[e.get("asin")] > 1:
            bad.append(f"entry {e['topic']!r}: ASIN {e.get('asin')!r} appears more than once")
    return bad


def evaluate_refill(pr: dict, files: list, check_runs: list, run_sha: str, repo: str,
                    products_text: str | None, merge_base_text: str | None,
                    modes: dict | None) -> Verdict:
    """Pure decision for a refill/* PR. `merge_base_text` is products.json at the merge base
    of the PR's base and head. A refill merge never dispatches publish."""
    why = _pr_problems(pr, run_sha, repo)
    if not is_refill_branch((pr.get("head") or {}).get("ref")):
        why.append("head branch is not refill/<slug>")

    why += _check_problems(check_runs)

    if not (isinstance(files, list) and len(files) == 1 and isinstance(files[0], dict)):
        why.append("the PR must change exactly one file, products.json")
    else:
        f = files[0]
        if f.get("filename") != "products.json" or f.get("status") != "modified" \
                or f.get("previous_filename") is not None:
            why.append("the only change allowed is a modified products.json")
        elif (modes or {}).get("products.json") != REGULAR_FILE_MODE:
            why.append("products.json is not a regular file at head")
        else:
            why += refill_products_problems(products_text, merge_base_text)

    return Verdict(not why, why, False)


# ------------------------------------------------------------------ gh plumbing

def gh(*args: str) -> str:
    out = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return out.stdout


def gh_json(*args: str):
    return json.loads(gh(*args))


def fetch_files(repo: str, number: int) -> list:
    lines = gh("api", "--paginate", f"repos/{repo}/pulls/{number}/files?per_page=100",
               "-q", ".[] | {filename, status, previous_filename} | @json")
    return [json.loads(ln) for ln in lines.splitlines() if ln.strip()]


CHECK_RUN_PAGES = 10            # 1,000 runs; a head SHA with more is held


def fetch_check_runs(repo: str, sha: str) -> list | None:
    """Every check run on `sha`, read page by page, or None when the pages do not add up
    to the endpoint's own total_count (the caller holds on None)."""
    runs, total = [], None
    for page in range(1, CHECK_RUN_PAGES + 1):
        data = gh_json("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}")
        total, batch = data.get("total_count"), data.get("check_runs")
        if type(total) is not int or not isinstance(batch, list):
            return None
        runs += batch
        if len(runs) >= total or len(batch) < 100:
            break
    return runs if len(runs) == total else None


def fetch_text(repo: str, path: str, ref: str) -> str | None:
    """Raw file content at a ref, or None when unavailable (the caller holds on None)."""
    try:
        return gh("api", "-H", "Accept: application/vnd.github.raw",
                  f"repos/{repo}/contents/{path}?ref={ref}")
    except subprocess.CalledProcessError:
        return None


def fetch_modes(repo: str, sha: str) -> dict | None:
    """{path: git mode} for every blob at `sha`; None if the tree is truncated."""
    data = gh_json("api", f"repos/{repo}/git/trees/{sha}?recursive=1")
    if data.get("truncated"):
        return None
    return {e["path"]: e.get("mode") for e in data.get("tree", []) if e.get("type") == "blob"}


def fetch_merge_base(repo: str, base_sha: str, head_sha: str) -> str | None:
    """SHA of the merge base of base...head (REST compare, `merge_base_commit.sha`)."""
    data = gh_json("api", f"repos/{repo}/compare/{base_sha}...{head_sha}")
    sha = (data.get("merge_base_commit") or {}).get("sha") if isinstance(data, dict) else None
    return sha if isinstance(sha, str) and sha else None


def merge_args(repo: str, number: int, head_sha: str) -> list:
    """The exact merge command. --match-head-commit makes a push after the green run fail
    instead of merging code CI never saw. No --admin, --auto or --delete-branch."""
    return ["pr", "merge", str(number), "--repo", repo, "--merge",
            "--match-head-commit", head_sha]


def publish_args(repo: str) -> list:
    return ["workflow", "run", "publish.yml", "--repo", repo, "--ref", BASE_BRANCH]


def summary(text: str) -> None:
    # Reasons can echo attacker-controlled names (file paths, labels). Collapse anything
    # non-printable -- newlines above all -- so no line can start a `::command` for the
    # runner and nothing can forge a log/summary line.
    text = "".join(ch if ch.isprintable() else "?" for ch in text)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass  # a summary must never break the gate


def process_refill(repo: str, number: int, pr: dict, run_sha: str, dry_run: bool) -> int:
    """The refill rule's reads. It never reads the issues endpoint. A failed read is a hold,
    the same as a failed evaluation."""
    head_sha = (pr.get("head") or {}).get("sha") or ""
    base_sha = (pr.get("base") or {}).get("sha") or ""
    try:
        files = fetch_files(repo, number)
        checks = fetch_check_runs(repo, head_sha) if head_sha else []
        products = fetch_text(repo, "products.json", head_sha) if head_sha else None
        merge_base = (fetch_merge_base(repo, base_sha, head_sha)
                      if head_sha and base_sha else None)
        base_products = fetch_text(repo, "products.json", merge_base) if merge_base else None
        modes = fetch_modes(repo, head_sha) if head_sha else None
        v = evaluate_refill(pr, files, checks, run_sha, repo, products, base_products, modes)
    except Exception as exc:  # fail closed: an unforeseen shape is a hold, never a merge
        summary(f"PR #{number}: left for a human -- gate error {type(exc).__name__}")
        return 0
    return act(repo, number, head_sha, v, dry_run)


def process(repo: str, number: int, run_sha: str, dry_run: bool) -> int:
    pr = gh_json("api", f"repos/{repo}/pulls/{number}")
    ref = (pr.get("head") or {}).get("ref")
    if isinstance(ref, str) and ref.startswith("refill/"):
        return process_refill(repo, number, pr, run_sha, dry_run)
    issue = gh_json("api", f"repos/{repo}/issues/{number}")
    files = fetch_files(repo, number)
    head_sha = (pr.get("head") or {}).get("sha") or ""
    base_sha = (pr.get("base") or {}).get("sha") or ""
    checks = fetch_check_runs(repo, head_sha) if head_sha else []
    names = [f.get("filename") for f in files if isinstance(f, dict)]
    prevs = [f.get("previous_filename") for f in files if isinstance(f, dict)]
    touches = "products.json" in names or "products.json" in prevs
    products = fetch_text(repo, "products.json", head_sha) if touches and head_sha else None
    base_products = fetch_text(repo, "products.json", base_sha) if touches and base_sha else None
    pin_texts = {p: fetch_text(repo, p, head_sha) for p in names
                 if isinstance(p, str) and _PIN_JSON.fullmatch(p)} if head_sha else {}
    modes = fetch_modes(repo, head_sha) if head_sha else None

    try:
        v = evaluate(pr, issue, files, checks, run_sha, repo, products, base_products,
                     pin_texts, modes)
    except Exception as exc:  # fail closed: an unforeseen shape is a hold, never a merge
        summary(f"PR #{number}: left for a human -- gate error {type(exc).__name__}")
        return 0
    return act(repo, number, head_sha, v, dry_run)


def act(repo: str, number: int, head_sha: str, v: Verdict, dry_run: bool) -> int:
    """Hold, dry-run, or merge on a verdict -- shared by both rules."""
    if not v.ok:
        summary(f"PR #{number}: left for a human -- " + "; ".join(v.reasons))
        return 0
    if dry_run:
        summary(f"PR #{number}: WOULD MERGE (publish dispatch: {v.publish}) -- dry run")
        return 0

    gh(*merge_args(repo, number, head_sha))
    if not gh_json("api", f"repos/{repo}/pulls/{number}").get("merged"):
        summary(f"PR #{number}: merge call returned but PR is not merged")
        return 1
    summary(f"PR #{number}: merged at {head_sha[:7]}")
    if v.publish:
        gh(*publish_args(repo))
        summary(f"PR #{number}: dispatched publish.yml")
    return 0


def main(argv: list) -> int:
    # The kill switch, mirrored from the workflow's `if:`. GitHub compares strings there
    # case-insensitively; here only the exact string "true" arms a merge. Anything else
    # evaluates and prints, as --dry-run does.
    dry_run = "--dry-run" in argv or os.environ.get("AUTOMERGE_ENABLED") != "true"
    repo = os.environ["REPO"]
    run_sha = os.environ["RUN_HEAD_SHA"]
    numbers = json.loads(os.environ.get("PR_NUMBERS") or "[]")
    if not numbers:
        summary("no same-repo PR attached to this CI run -- nothing to do")
        return 0
    rc = 0
    for n in numbers:
        if not isinstance(n, int):
            summary(f"ignoring non-integer PR number {n!r}")
            continue
        try:
            rc |= process(repo, n, run_sha, dry_run)
        except subprocess.CalledProcessError as exc:
            summary(f"PR #{n}: gh failed ({exc.cmd[:3]}): {(exc.stderr or '').strip()[:300]}")
            rc = 1
        except Exception as exc:  # one bad PR must not stop the others; it is never merged
            summary(f"PR #{n}: left for a human -- error {type(exc).__name__}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
