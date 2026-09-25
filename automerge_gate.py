#!/usr/bin/env python3
"""Auto-merge gate for routine Stage-1 content PRs (run by .github/workflows/automerge.yml).

A PR is merged only when EVERY condition holds; anything else is left for a human
(exit 0, reason printed). The decision logic is `evaluate()`, a pure function so the
case table in test_pipeline.py can attack it without a network.

Conditions (all required):
  1. PR is open, not a draft, same-repo, based on main, not labelled `no-automerge`.
  2. Opened by the Claude GitHub App: issue `performed_via_github_app.slug == "claude"`
     AND user.login in ALLOWED_AUTHORS. (The PR *author* alone cannot tell the routine
     from the Director's own `gh` PRs -- both show up as DMoneyOH; only the app
     attribution differs. GitHub sets it from the token used, so it cannot be spoofed
     from the PR text, branch name or commit author.)
  3. The named `pytest` check (app github-actions) succeeded on the exact head SHA the
     triggering CI run tested, and no other check on that SHA is pending or failing.
     Zero matching checks is a hold, never a pass.
  4. Every changed path -- and every rename source -- fully matches ALLOWED_PATHS.
  5. If products.json is touched, the head version contains no NEEDS_ASIN/NEEDS_IMAGE
     placeholder (a held placeholder still burns a publish slot).

After a successful merge, `publish.yml` is dispatched, but only when the PR ADDED a
_posts/DRAFT-*.md file. A merge made with GITHUB_TOKEN does not fire publish.yml's
push trigger, and a products.json-only merge must not publish a leftover draft early.

Env: GH_TOKEN, REPO (owner/name), RUN_HEAD_SHA, PR_NUMBERS (JSON list).
`--dry-run` evaluates and prints; it never merges or dispatches.
"""
import json
import os
import re
import subprocess
import sys
from typing import NamedTuple

ALLOWED_AUTHORS = frozenset({"DMoneyOH"})
ALLOWED_APP_SLUGS = frozenset({"claude"})
BASE_BRANCH = "main"
REQUIRED_CHECK = "pytest"
CHECK_APP_SLUG = "github-actions"
OPT_OUT_LABEL = "no-automerge"
MAX_FILES = 30

_SLUG = r"[a-z0-9]+(?:-[a-z0-9]+)*"
# What routine Stage-1 PRs actually touch (PRs #85, #105, #111): the draft, its pin
# spec, its pin image. Top-level only -- _pin_queue/sent/ and _pin_queue/.fired/ are
# pipeline state, not content. products.json is the refill/queue file. Exact
# full-match patterns, never prefixes: `_posts/DRAFT-x.md.bak`, `_posts/sub/DRAFT-x.md`
# and `_pin_queue/../x` all fail.
ALLOWED_PATHS = (
    re.compile(rf"_posts/DRAFT-{_SLUG}\.md"),
    re.compile(rf"_pin_queue/{_SLUG}\.json"),
    re.compile(rf"assets/images/pins/{_SLUG}\.jpg"),
    re.compile(r"products\.json"),
)
_DRAFT = re.compile(rf"_posts/DRAFT-{_SLUG}\.md")
PLACEHOLDER_MARKERS = ("NEEDS_ASIN", "NEEDS_IMAGE")
_OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


class Verdict(NamedTuple):
    ok: bool
    reasons: list
    publish: bool


def path_allowed(path) -> bool:
    return isinstance(path, str) and any(p.fullmatch(path) for p in ALLOWED_PATHS)


def evaluate(pr: dict, issue: dict, files: list, check_runs: list,
             run_sha: str, repo: str, products_text: str | None = None) -> Verdict:
    """Pure decision. `pr`=REST pulls/{n}, `issue`=REST issues/{n}, `files`=REST
    pulls/{n}/files entries, `check_runs`=check_runs of the PR head SHA."""
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
    if any((lb or {}).get("name") == OPT_OUT_LABEL for lb in pr.get("labels") or []):
        why.append(f"labelled {OPT_OUT_LABEL}")

    author = (issue.get("user") or {}).get("login")
    app = (issue.get("performed_via_github_app") or {}).get("slug")
    if author not in ALLOWED_AUTHORS:
        why.append(f"author {author!r} not allowlisted")
    if app not in ALLOWED_APP_SLUGS:
        why.append(f"not opened via the Claude GitHub App (app={app!r})")

    # -- CI: the named check must exist and pass; nothing else may be pending/red
    named = [c for c in check_runs if c.get("name") == REQUIRED_CHECK
             and (c.get("app") or {}).get("slug") == CHECK_APP_SLUG]
    if not named:
        why.append(f"no {REQUIRED_CHECK!r} check from {CHECK_APP_SLUG} on the head SHA")
    elif not all(c.get("status") == "completed" and c.get("conclusion") == "success"
                 for c in named):
        why.append(f"{REQUIRED_CHECK!r} check is not green")
    for c in check_runs:
        if c.get("status") != "completed" or c.get("conclusion") not in _OK_CONCLUSIONS:
            why.append(f"check {c.get('name')!r} is not green "
                       f"({c.get('status')}/{c.get('conclusion')})")

    # -- Paths
    if not files:
        why.append("empty file list")
    elif len(files) > MAX_FILES:
        why.append(f"more than {MAX_FILES} changed files")
    paths = [p for f in files for p in (f.get("filename"), f.get("previous_filename"))
             if p is not None]
    why += [f"path not on allowlist: {p}" for p in paths if not path_allowed(p)]
    touches_products = any(f.get("filename") == "products.json"
                           or f.get("previous_filename") == "products.json" for f in files)
    if touches_products:
        if products_text is None:
            why.append("products.json changed but its head content was not available")
        elif any(m in products_text for m in PLACEHOLDER_MARKERS):
            why.append("products.json still carries NEEDS_ASIN/NEEDS_IMAGE placeholders")

    publish = any(f.get("status") == "added" and _DRAFT.fullmatch(f.get("filename") or "")
                  for f in files)
    return Verdict(not why, why, publish)


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


def fetch_check_runs(repo: str, sha: str) -> list:
    data = gh_json("api", f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    return data.get("check_runs") or []


def fetch_products(repo: str, sha: str) -> str | None:
    try:
        return gh("api", "-H", "Accept: application/vnd.github.raw",
                  f"repos/{repo}/contents/products.json?ref={sha}")
    except subprocess.CalledProcessError:
        return None


def summary(text: str) -> None:
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass  # a summary must never break the gate


def process(repo: str, number: int, run_sha: str, dry_run: bool) -> int:
    pr = gh_json("api", f"repos/{repo}/pulls/{number}")
    issue = gh_json("api", f"repos/{repo}/issues/{number}")
    files = fetch_files(repo, number)
    head_sha = (pr.get("head") or {}).get("sha") or ""
    checks = fetch_check_runs(repo, head_sha) if head_sha else []
    touches = any("products.json" in (f.get("filename"), f.get("previous_filename"))
                  for f in files)
    products = fetch_products(repo, head_sha) if touches and head_sha else None

    v = evaluate(pr, issue, files, checks, run_sha, repo, products)
    if not v.ok:
        summary(f"PR #{number}: left for a human -- " + "; ".join(v.reasons))
        return 0
    if dry_run:
        summary(f"PR #{number}: WOULD MERGE (publish dispatch: {v.publish}) -- dry run")
        return 0

    # --match-head-commit: a push after the green run makes this fail instead of
    # merging code CI never saw. No --delete-branch, no --admin, no --auto.
    gh("pr", "merge", str(number), "--repo", repo, "--merge",
       "--match-head-commit", head_sha)
    if not gh_json("api", f"repos/{repo}/pulls/{number}").get("merged"):
        summary(f"PR #{number}: merge call returned but PR is not merged")
        return 1
    summary(f"PR #{number}: merged at {head_sha[:7]}")
    if v.publish:
        gh("workflow", "run", "publish.yml", "--repo", repo, "--ref", BASE_BRANCH)
        summary(f"PR #{number}: dispatched publish.yml")
    return 0


def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
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
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
