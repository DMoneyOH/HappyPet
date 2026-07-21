# Claude Routine Stage 1 — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic "plumbing CLI" and the agent operating-procedure that let a supervised Claude loop (Opus writes, Sonnet reviews, Opus rewrites) produce a valid HappyPet article, stage it exactly as today's Stage 1 does, and open a PR — so we can prove the bar is reachable and measure per-article cost before automating.

**Architecture:** Keep every deterministic function in `generate_posts.py` (152-test-covered) and expose the pieces the agent needs through a thin CLI (`stage1_cli.py`). The Claude agent does the writing/reviewing/rewriting with Claude models (no OpenRouter); Python only builds prompts, runs the authoritative gate, and stages files. Refactor three blocks out of `main()` into reusable functions so both legacy `main()` and the new CLI call the same code.

**Tech Stack:** Python 3.12, `argparse`, `pytest`. Windows dev shell; run tests with `./.venv/Scripts/python.exe -m pytest`. All file reads/writes use `encoding="utf-8"` (source contains literal U+2014/U+2013).

**Scope:** Phase 1 only (supervised, `auto_merge=off`). Phase 2 (scheduled cloud routine + auto-merge + cron + `generate.yml` retirement) is a separate plan, gated on Phase 1 results and the §9.1 feasibility checks in the spec.

**Spec:** `docs/superpowers/specs/2026-07-21-claude-routine-stage1-design.md`

---

## File Structure

- **Modify `generate_posts.py`** — add one new function and extract three reusable functions from `main()`:
  - `authoritative_gate(scorecard, content)` — new; pass computed from scores + hard-checks, ignoring the reviewer's `pass` boolean.
  - `select_next_topic(products, used_slugs)` — extracted topic-selection + dedup.
  - `build_writer_inputs(slug, product)` — extracted writer prompt assembly (system + user), internal-only (no Groq alternatives call).
  - `stage_article(slug, product, body, pin_desc, index)` — extracted staging (front-matter + pin image + pin-queue + draft-last write).
  - `main()` is rewired to call the four functions (behavior unchanged; 152 tests stay green).
- **Create `stage1_cli.py`** — argparse CLI with verbs `next-topic`, `review-prompt`, `gate`, `rewrite-prompt`, `stage`. Thin: each verb calls a `generate_posts` function and prints JSON/text. No model calls live here.
- **Create `.claude/skills/happypet-stage1/SKILL.md`** — the agent operating procedure (the loop): how to call the CLI, spawn the Sonnet reviewer subagent, iterate, and open the PR. This is the "routine" for Phase 1 (run by the agent supervised).
- **Modify `test_pipeline.py`** — tests for `authoritative_gate`, `select_next_topic`, `build_writer_inputs`, `stage_article`.
- **Create `test_stage1_cli.py`** — tests for the CLI verbs (subprocess-level, model outputs stubbed via files).

**Conventions:** branch off `main` as `claude/happypet-recovery-40-stage1-routine` (already created; this plan's commits land there). Conventional commits ending `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Run the full suite after each task.

---

## Task 1: `authoritative_gate()` — pass from scores + hard-checks, ignore reviewer `pass`

**Files:**
- Modify: `generate_posts.py` (add after `evaluate_scorecard`, which ends at line 697)
- Test: `test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

Add to `test_pipeline.py`:

```python
class TestAuthoritativeGate(unittest.TestCase):
    """authoritative_gate computes pass from scores + hard-checks and IGNORES
    the reviewer's `pass` boolean -- unlike evaluate_scorecard, a reviewer
    saying pass=false cannot hold an otherwise-clean, on-standard article."""

    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _card(self, hv=4, wa=4, re=4, ac=4, **extra):
        card = {"scores": {"human_voice": hv, "warmth": wa,
                           "readability": re, "accuracy": ac}}
        card.update(extra)
        return card

    def test_reviewer_pass_false_does_not_hold_a_clean_on_standard_article(self):
        # scores all clear the bar, body clean -> PASS even though reviewer said fail
        passed, flags = self.gp.authoritative_gate(
            self._card(**{"pass": False, "ai_patterns_found": ["em dash present"]}),
            "clean body without the forbidden character.")
        self.assertTrue(passed)

    def test_score_below_minimum_fails(self):
        passed, flags = self.gp.authoritative_gate(self._card(hv=2), "clean body")
        self.assertFalse(passed)
        self.assertTrue(any("human_voice=2" in str(f) for f in flags))

    def test_real_em_dash_in_body_fails(self):
        passed, flags = self.gp.authoritative_gate(self._card(), "has an — em dash")
        self.assertFalse(passed)
        self.assertIn("em_dash_in_body", flags)

    def test_fabrication_flag_fails(self):
        passed, _ = self.gp.authoritative_gate(
            self._card(flags=["fabricated statistic in section 2"]), "clean body")
        self.assertFalse(passed)

    def test_missing_score_fails(self):
        passed, _ = self.gp.authoritative_gate(self._card(hv=None), "clean body")
        self.assertFalse(passed)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestAuthoritativeGate -v`
Expected: FAIL with `AttributeError: module 'generate_posts' has no attribute 'authoritative_gate'`

- [ ] **Step 3: Implement `authoritative_gate`**

Add to `generate_posts.py` immediately after `evaluate_scorecard` (after line 697):

```python
def authoritative_gate(scorecard: dict, content: str) -> tuple:
    """Decide pass/fail for the internal Claude routine WITHOUT trusting the
    reviewer's self-reported `pass`. Unlike evaluate_scorecard (which seeds from
    `pass` and can only downgrade -- the asymmetry that held a clean article on a
    hallucinated em-dash veto), this starts from PASS and downgrades only on:
      - a numeric score below its REVIEW_SCORE_MINIMUMS floor,
      - a real em dash in the actual body (deterministic, not the model's count),
      - an accuracy/fabrication keyword in the flags.
    Returns (passed: bool, flags: list).
    """
    passed = True
    flags  = list(scorecard.get("flags", []))
    scores = scorecard.get("scores", {}) or {}

    for key, minimum in REVIEW_SCORE_MINIMUMS.items():
        val = scores.get(key)
        if not isinstance(val, (int, float)) or val < minimum:
            passed = False
            flags.append(f"{key}={val} below minimum {minimum}")

    if "—" in content:  # real U+2014 em dash in the body
        passed = False
        flags.append("em_dash_in_body")

    accuracy_keywords = ("fabricat", "unverif", "invent", "statistic", "percentag",
                         "specific number", "no source", "not verif", "made up",
                         "cited", "claimed", "without source")
    if flags and any(kw in " ".join(str(f) for f in flags).lower()
                     for kw in accuracy_keywords):
        passed = False

    return passed, flags
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestAuthoritativeGate -v`
Expected: 5 passed

- [ ] **Step 5: Run full suite (no regressions)**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: `157 passed` (152 + 5)

- [ ] **Step 6: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "feat(stage1): authoritative_gate -- pass from scores+hard-checks, ignore reviewer pass

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Extract `select_next_topic()` from `main()`

**Files:**
- Modify: `generate_posts.py` (add helper near `build_used_slugs`, line 536; rewire `main()` lines 1402-1424)
- Test: `test_pipeline.py`

**Context:** `main()` builds `topics` from `products.values()` filtered to entries with `topic/title/keyword/format`, drops `used_slugs`, then slices `[:max_articles]` (lines 1402-1424). The CLI needs just "the next unpublished topic". Extract a pure helper; `main()` keeps its cap slice by calling it in order.

- [ ] **Step 1: Write the failing test**

Add to `test_pipeline.py`:

```python
class TestSelectNextTopic(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def _products(self):
        return {
            "a": {"topic": "a", "title": "A", "keyword": "ka", "format": "single_review"},
            "b": {"topic": "b", "title": "B", "keyword": "kb", "format": "roundup"},
            "c": {"topic": "c", "title": "C"},  # missing keyword/format -> skipped
        }

    def test_returns_first_unpublished_valid_topic(self):
        slug, product = self.gp.select_next_topic(self._products(), used_slugs=set())
        self.assertEqual(slug, "a")
        self.assertEqual(product["format"], "single_review")

    def test_skips_used_slugs(self):
        slug, product = self.gp.select_next_topic(self._products(), used_slugs={"a"})
        self.assertEqual(slug, "b")

    def test_skips_entries_missing_required_fields(self):
        # only 'c' left unused, but 'c' is invalid -> None
        slug_product = self.gp.select_next_topic(self._products(), used_slugs={"a", "b"})
        self.assertIsNone(slug_product)

    def test_returns_none_when_queue_empty(self):
        self.assertIsNone(self.gp.select_next_topic({}, used_slugs=set()))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestSelectNextTopic -v`
Expected: FAIL with `AttributeError: ... has no attribute 'select_next_topic'`

- [ ] **Step 3: Implement `select_next_topic`**

Add to `generate_posts.py` after `build_used_slugs` (after line ~545):

```python
def select_next_topic(products: dict, used_slugs: set):
    """Return (slug, product) for the first products.json entry that is unpublished
    and has the required fields, in dict order (same order main() consumes). Return
    None when nothing is eligible. Mirrors the topic filter + dedup in main()."""
    required = ("topic", "title", "keyword", "format")
    for slug, p in products.items():
        if slug in used_slugs:
            continue
        if all(k in p for k in required):
            return slug, p
    return None
```

- [ ] **Step 4: Rewire `main()` to use it (behavior unchanged)**

In `generate_posts.py`, replace lines 1402-1424 (the `topics = [...]` comprehension through the `topics = topics[:max_articles]` cap slice) with:

```python
        # TOPICS entirely from products.json -- no hardcoded list. Build the
        # capped worklist by repeatedly selecting the next eligible topic so the
        # ordering/dedup rule lives in one place (select_next_topic).
        used_slugs = build_used_slugs()
        topics = []
        remaining = dict(products)
        while len(topics) < max_articles:
            picked = select_next_topic(remaining, used_slugs)
            if picked is None:
                break
            slug, p = picked
            topics.append((p["topic"], p["title"], p["keyword"], p["format"]))
            remaining.pop(slug, None)
        log(f"Unpublished topics queued this run: {len(topics)}")
```

Note: if `used_slugs`/`build_used_slugs()` is already computed later in `main()`, delete the now-duplicate assignment so it is defined once here. Search for `used_slugs =` and keep only this one.

- [ ] **Step 5: Run full suite (refactor must not regress)**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestSelectNextTopic test_pipeline.py -q`
Expected: `161 passed` (157 + 4). If any previously-passing test fails, the extraction changed behavior — reconcile before continuing.

- [ ] **Step 6: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "refactor(stage1): extract select_next_topic; main() consumes it

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Extract `build_writer_inputs()` (internal-only, no Groq)

**Files:**
- Modify: `generate_posts.py` (add near `make_prompt`, line 965; rewire `main()` lines 1468-1502)
- Test: `test_pipeline.py`

**Context:** `main()` assembles the writer prompt at lines 1468-1502: related-link lookup, roundup alternatives (`runners_up` from products.json OR a Groq `find_alternative_products` fallback), `make_prompt(...)`, then `{{ALTERNATIVE_PRODUCTS}}` substitution. Internal-only means **drop the Groq fallback** — use `runners_up` when present, else the static "well-known brands" instruction. Extract into a pure function returning the system + user prompt.

- [ ] **Step 1: Write the failing test**

Add to `test_pipeline.py`:

```python
class TestBuildWriterInputs(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_single_review_inputs_have_system_and_user(self):
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dogs",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc"}
        out = self.gp.build_writer_inputs("best-x", product)
        self.assertEqual(out["system"], self.gp.GENERATOR_SYSTEM_PROMPT)
        self.assertIn("Best X", out["user"])
        self.assertEqual(out["fmt"], "single_review")
        self.assertEqual(out["species"], "dog")
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])  # substituted

    def test_roundup_uses_products_json_runners_up_not_groq(self):
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog", "runners_up": "MatA;MatB;MatC"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("MatA", out["user"])
        self.assertIn("EXACTLY 3", out["user"])
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])

    def test_roundup_without_runners_up_uses_static_fallback_no_groq(self):
        product = {"topic": "best-mats", "title": "Best Mats", "keyword": "best mats",
                   "format": "roundup", "name": "TopMat", "category": "dogs",
                   "species": "dog"}
        out = self.gp.build_writer_inputs("best-mats", product)
        self.assertIn("well-known brands", out["user"])
        self.assertNotIn("{{ALTERNATIVE_PRODUCTS}}", out["user"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestBuildWriterInputs -v`
Expected: FAIL with `AttributeError: ... has no attribute 'build_writer_inputs'`

- [ ] **Step 3: Implement `build_writer_inputs`**

Add to `generate_posts.py` immediately before `make_prompt` (before line 965):

```python
def build_writer_inputs(slug: str, product: dict) -> dict:
    """Assemble the writer's system+user prompts for one topic, internal-only.
    Roundup alternatives come from products.json `runners_up`; when absent, a
    static instruction is used instead of the (external) Groq fallback. Returns
    {system, user, title, keyword, fmt, species, affiliate_url}."""
    title    = product["title"]
    keyword  = product["keyword"]
    fmt      = product["format"]
    species  = product.get("species", "dog")
    category = product.get("category", "")

    related_url, related_anchor = find_related_published_slug(slug, category)
    user = make_prompt(title, keyword, slug, fmt, product, related_url, related_anchor)

    if "{{ALTERNATIVE_PRODUCTS}}" in user:
        runners_up = product.get("runners_up", "")
        if runners_up:
            if ";" in runners_up:
                alt_count = len([a for a in runners_up.split(";") if a.strip()])
            else:
                alt_count = len([ln for ln in runners_up.splitlines() if ln.strip()])
            alt_constraint = (
                "EXACTLY " + str(alt_count) + " alternative product(s) listed below. "
                "Use ONLY these " + str(alt_count) + " product(s). "
                "Do NOT add, invent, or substitute any others.\n" + runners_up
            )
            user = user.replace("{{ALTERNATIVE_PRODUCTS}}", alt_constraint)
        else:
            user = user.replace(
                "{{ALTERNATIVE_PRODUCTS}}",
                "EXACTLY 3 alternatives -- use well-known brands you are confident "
                "exist. Do not fabricate products.")

    return {"system": GENERATOR_SYSTEM_PROMPT, "user": user, "title": title,
            "keyword": keyword, "fmt": fmt, "species": species,
            "affiliate_url": product.get("affiliate_url", "")}
```

- [ ] **Step 4: Rewire `main()` to use it**

In `generate_posts.py`, replace lines 1468-1502 (the `alternatives_text` block through `content = call_generator(...)`) so the alternatives/prompt assembly uses the helper, keeping the legacy generator call:

```python
                writer = build_writer_inputs(slug, product)
                prompt = writer["user"]
                content = call_generator(prompt, groq_key, system=writer["system"])
                log(f"  [timing] generate: {time.monotonic()-_t0:.1f}s")
```

(This removes the inline Groq `find_alternative_products` fallback from the legacy path too — acceptable, since roundups without `runners_up` are rare and the static instruction is safer than a fabricating free model.)

- [ ] **Step 5: Run full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: `164 passed` (161 + 3). Reconcile any regression before continuing (a test asserting the Groq fallback path would now need updating to the static instruction).

- [ ] **Step 6: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "refactor(stage1): extract build_writer_inputs (internal-only, drop Groq alt fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Extract `stage_article()` from `main()`

**Files:**
- Modify: `generate_posts.py` (add after `persist_generated_article`, line 1364; rewire `main()` lines 1544-1582)
- Test: `test_pipeline.py`

**Context:** `main()` stages a passed article at lines 1544-1582: build front-matter, strip leading `---`, compute `article_url`, generate the pin image (`make_pin_for_post`, with an ASIN→CDN url fallback), build `pin_data`, then `persist_generated_article` (draft-last). Extract verbatim into a function taking the already-cleaned `body` and the `pin_desc`.

- [ ] **Step 1: Write the failing test**

Add to `test_pipeline.py`:

```python
class TestStageArticle(unittest.TestCase):
    def setUp(self):
        import generate_posts as gp
        self.gp = gp

    def test_stages_draft_and_pin_queue(self):
        import tempfile, json
        from pathlib import Path
        from unittest.mock import patch
        gp = self.gp
        product = {"topic": "best-x", "title": "Best X", "keyword": "best x",
                   "format": "single_review", "name": "The X", "category": "dogs",
                   "species": "dog", "affiliate_url": "https://amzn.to/abc"}
        body = "## Heading\n\n" + ("word " * 800)  # >700 words, >2000 chars
        with tempfile.TemporaryDirectory() as td:
            posts = Path(td) / "_posts"; posts.mkdir()
            pinq  = Path(td) / "_pin_queue"; pinq.mkdir()
            with patch.object(gp, "POSTS_DIR", posts), \
                 patch.object(gp, "REPO_DIR", Path(td)), \
                 patch.object(gp, "PIN_GEN_AVAILABLE", False):
                out = gp.stage_article("best-x", product, body,
                                       pin_desc="Great mat for dogs.", index=0)
            draft = posts / "DRAFT-best-x.md"
            self.assertTrue(draft.exists())
            text = draft.read_text(encoding="utf-8")
            self.assertIn('layout: post', text)
            self.assertIn('affiliate_url: "https://amzn.to/abc"', text)
            pin_json = pinq / "best-x.json"
            self.assertTrue(pin_json.exists())
            data = json.loads(pin_json.read_text(encoding="utf-8"))
            self.assertEqual(data["slug"], "best-x")
            self.assertEqual(out["draft_path"], draft)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py::TestStageArticle -v`
Expected: FAIL with `AttributeError: ... has no attribute 'stage_article'`

- [ ] **Step 3: Implement `stage_article`**

Add to `generate_posts.py` after `persist_generated_article` (after line 1364):

```python
def stage_article(slug: str, product: dict, body: str, pin_desc: str,
                  index: int = 0) -> dict:
    """Stage one passed article exactly as main() does: front-matter + pin image
    + pin-queue entry + draft-last write. `body` must be the final, review-clean
    article text (no PIN_DESC line). Returns {draft_path, pin_queue_path}."""
    title    = product["title"]
    keyword  = product["keyword"]
    species  = product.get("species", "dog")
    category = product.get("category", "")

    fm = front_matter(title, keyword, product.get("affiliate_url", ""), slug,
                      species, category, pin_desc, product.get("image", ""),
                      build_pin_image_url_for_queue(slug),
                      chewy_url=product.get("chewy_url") or "")

    content_clean = body.lstrip()
    while content_clean.startswith("---"):
        content_clean = content_clean[3:].lstrip()

    article_url = build_url(slug, utm=True)
    asin    = product.get("asin", "")
    pin_url = product.get("image", "")
    if asin:
        pin_url = f"https://images-na.ssl-images-amazon.com/images/P/{asin}.01.LZZZZZZZ.jpg"
    if PIN_GEN_AVAILABLE:
        try:
            pin_url = make_pin_for_post(title, pin_desc, pin_url, category, slug, index)
            log_pin(f"  PIN: {pin_url}")
        except Exception as pe:
            log_pin(f"  pin generation failed: {pe}", "WARN")

    pin_data = {
        "title": title, "article_url": article_url, "description": pin_desc,
        "image_url": build_pin_image_url_for_queue(slug), "species": species,
        "slug": slug, "topical_sheet": product.get("topical_sheet", ""),
    }
    draft_path = POSTS_DIR / f"DRAFT-{slugify(slug)}.md"
    pin_queue_path = REPO_DIR / "_pin_queue" / f"{slug}.json"
    persist_generated_article(draft_path, fm + "\n" + content_clean,
                              pin_queue_path, pin_data)
    log(f"  SAVED {draft_path.name} + staged pin {slug}.json")
    return {"draft_path": draft_path, "pin_queue_path": pin_queue_path}
```

- [ ] **Step 4: Rewire `main()` to use it**

In `generate_posts.py`, replace lines 1544-1582 (from `fname = f"DRAFT-...` through the `persist_generated_article(...)` call and its `log(...)`) with:

```python
                stage_article(slug, product, content, pin_desc, index=generated)
```

(`content` here is already the review-passed, scrubbed body; `pin_desc` and `generated` are already in scope.)

- [ ] **Step 5: Run full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: `165 passed` (164 + 1). Reconcile any regression.

- [ ] **Step 6: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "refactor(stage1): extract stage_article; main() delegates staging

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `stage1_cli.py` — the plumbing CLI

**Files:**
- Create: `stage1_cli.py`
- Test: `test_stage1_cli.py`

**Interface (verbs):**
- `next-topic` → JSON `{slug, title, keyword, fmt, species, system, user, affiliate_url}` for the next unpublished topic, or `{}` if the queue is drained.
- `review-prompt --body <file> --title <t> --keyword <k>` → prints the reviewer prompt text.
- `gate --body <file> --scorecard <file>` → JSON `{passed, flags, scrubbed_body}`.
- `rewrite-prompt --body <file> --instructions <file> [--title --keyword --affiliate-url --product-name]` → prints the rewrite prompt text.
- `stage --slug <s> --body <file> [--pin-desc <d>]` → JSON `{draft_path, pin_queue_path}`; runs `validate_output("review", ...)` first and exits non-zero on contract failure.

- [ ] **Step 1: Write the failing tests**

Create `test_stage1_cli.py`:

```python
import json, subprocess, sys, tempfile
from pathlib import Path

PY = "./.venv/Scripts/python.exe"

def run(*args, cwd=None):
    return subprocess.run([PY, "stage1_cli.py", *args],
                          capture_output=True, text=True, cwd=cwd)

def test_gate_passes_clean_on_standard_article():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("clean body text", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({"pass": False,
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        assert r.returncode == 0, r.stderr
        out = json.loads(r.stdout)
        assert out["passed"] is True

def test_gate_fails_on_em_dash():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("has — dash", encoding="utf-8")
        card = Path(td) / "card.json"
        card.write_text(json.dumps({
            "scores": {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}),
            encoding="utf-8")
        r = run("gate", "--body", str(body), "--scorecard", str(card))
        out = json.loads(r.stdout)
        assert out["passed"] is False
        assert "em_dash_in_body" in out["flags"]

def test_review_prompt_contains_title_and_rubric():
    with tempfile.TemporaryDirectory() as td:
        body = Path(td) / "body.md"; body.write_text("article body", encoding="utf-8")
        r = run("review-prompt", "--body", str(body),
                "--title", "Best Dog Mats", "--keyword", "dog mats")
        assert r.returncode == 0, r.stderr
        assert "Best Dog Mats" in r.stdout
        assert "human_voice" in r.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest test_stage1_cli.py -v`
Expected: FAIL (`stage1_cli.py` does not exist → non-zero returncode, `json.loads` errors)

- [ ] **Step 3: Implement `stage1_cli.py`**

```python
#!/usr/bin/env python3
"""Plumbing CLI for the internal Claude Stage-1 routine. Exposes the deterministic
pieces the agent needs (prompt building, the authoritative gate, staging) over a
thin command surface. No model calls happen here -- the agent supplies the write,
review, and rewrite text using Claude models."""
import argparse, json, sys
from pathlib import Path

import generate_posts as gp


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def cmd_next_topic(args) -> int:
    products = gp.load_products()
    used = gp.build_used_slugs()
    picked = gp.select_next_topic(products, used)
    if picked is None:
        print(json.dumps({}))
        return 0
    slug, product = picked
    writer = gp.build_writer_inputs(slug, product)
    print(json.dumps({
        "slug": slug, "title": writer["title"], "keyword": writer["keyword"],
        "fmt": writer["fmt"], "species": writer["species"],
        "affiliate_url": writer["affiliate_url"],
        "system": writer["system"], "user": writer["user"],
    }))
    return 0


def cmd_review_prompt(args) -> int:
    body = _read(args.body)
    print(gp.make_review_prompt(args.title, args.keyword, body))
    return 0


def cmd_gate(args) -> int:
    body = _read(args.body)
    scorecard = json.loads(_read(args.scorecard))
    scrubbed = gp.scrub_typography(body)
    passed, flags = gp.authoritative_gate(scorecard, scrubbed)
    print(json.dumps({"passed": passed, "flags": flags, "scrubbed_body": scrubbed}))
    return 0


def cmd_rewrite_prompt(args) -> int:
    body = _read(args.body)
    instructions = _read(args.instructions)
    print(gp.make_rewrite_prompt(args.title, args.keyword, body, instructions,
                                 affiliate_url=args.affiliate_url,
                                 product_name=args.product_name))
    return 0


def cmd_stage(args) -> int:
    body = gp.scrub_typography(_read(args.body))
    products = gp.load_products()
    product = products.get(args.slug)
    if product is None:
        print(f"ERROR: unknown slug {args.slug!r}", file=sys.stderr)
        return 2
    pin_desc = gp.clean_pin_desc(args.pin_desc or f"{product['title']} - reviews and buying guide.",
                                 product.get("species", "dog"))
    try:
        gp.validate_output("review", body, args.slug,
                           affiliate_url=product.get("affiliate_url", ""))
    except gp.GenerationStageError as e:
        print(f"ERROR: content contract failed: {e}", file=sys.stderr)
        return 3
    out = gp.stage_article(args.slug, product, body, pin_desc)
    print(json.dumps({"draft_path": str(out["draft_path"]),
                      "pin_queue_path": str(out["pin_queue_path"])}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="stage1_cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("next-topic").set_defaults(func=cmd_next_topic)

    rp = sub.add_parser("review-prompt")
    rp.add_argument("--body", required=True); rp.add_argument("--title", required=True)
    rp.add_argument("--keyword", required=True); rp.set_defaults(func=cmd_review_prompt)

    g = sub.add_parser("gate")
    g.add_argument("--body", required=True); g.add_argument("--scorecard", required=True)
    g.set_defaults(func=cmd_gate)

    rw = sub.add_parser("rewrite-prompt")
    rw.add_argument("--body", required=True); rw.add_argument("--instructions", required=True)
    rw.add_argument("--title", default=""); rw.add_argument("--keyword", default="")
    rw.add_argument("--affiliate-url", dest="affiliate_url", default="")
    rw.add_argument("--product-name", dest="product_name", default="")
    rw.set_defaults(func=cmd_rewrite_prompt)

    st = sub.add_parser("stage")
    st.add_argument("--slug", required=True); st.add_argument("--body", required=True)
    st.add_argument("--pin-desc", dest="pin_desc", default="")
    st.set_defaults(func=cmd_stage)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_stage1_cli.py -v`
Expected: 3 passed

- [ ] **Step 5: Smoke-test `next-topic` against the real repo**

Run: `./.venv/Scripts/python.exe stage1_cli.py next-topic`
Expected: one JSON object with a real `slug` (e.g. `best-dog-cooling-mat`) and a non-empty `user` prompt. (Confirms `load_products`/`build_used_slugs`/`build_writer_inputs` wire together on live data.)

- [ ] **Step 6: Run full suite + commit**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (165 + 3).

```bash
git add stage1_cli.py test_stage1_cli.py
git commit -m "feat(stage1): plumbing CLI (next-topic/review-prompt/gate/rewrite-prompt/stage)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: The routine operating-procedure (`SKILL.md`)

**Files:**
- Create: `.claude/skills/happypet-stage1/SKILL.md`

**Context:** This is the agent's loop — documentation, not code. It is the same procedure Phase 2 will schedule. It must be explicit enough that a fresh Opus agent runs the loop with no other context.

- [ ] **Step 1: Write the procedure**

Create `.claude/skills/happypet-stage1/SKILL.md`:

````markdown
---
name: happypet-stage1
description: Run one HappyPet Stage-1 article end to end -- write (Opus), review (Sonnet), rewrite until it passes the authoritative gate, stage it, and open a PR. Use when producing a HappyPet article via the internal Claude routine.
---

# HappyPet Stage 1 — internal write/review/rewrite routine

Produce ONE publishable article and open a PR. All model work is done by you
(Opus) and a Sonnet reviewer subagent; `stage1_cli.py` does every deterministic
step. Never call OpenRouter or any external model. Run from the repo root with
`./.venv/Scripts/python.exe`.

## Config
- `AUTO_MERGE`: `off` for Phase 1 (open the PR and stop). Do NOT merge.
- `MAX_ATTEMPTS`: 4 (write + up to 3 rewrites).
- `TARGET_BAR`: aim for reviewer scores of 4 on all axes; the enforced floor is
  whatever `stage1_cli gate` returns (currently 3). Record the best scores reached.

## Loop
1. **Pick topic:** `python stage1_cli.py next-topic`. If it prints `{}`, the queue
   is drained — stop and report. Otherwise keep the JSON (`slug`, `system`, `user`,
   `title`, `keyword`, `affiliate_url`).
2. **Write (Opus, you):** produce the article body from `system` + `user`. Write it
   to `LOOP/body.md` (utf-8). It must satisfy the writing rules in `system`.
   Also write a one-line Pinterest description to `LOOP/pindesc.txt`.
3. **Review (Sonnet subagent):**
   - `python stage1_cli.py review-prompt --body LOOP/body.md --title "<title>" --keyword "<keyword>" > LOOP/revprompt.txt`
   - Dispatch a subagent with `model: sonnet` whose entire task is: read
     `LOOP/revprompt.txt`, follow it, and return ONLY the scorecard JSON. Save its
     reply to `LOOP/card.json`.
4. **Gate (deterministic):** `python stage1_cli.py gate --body LOOP/body.md --scorecard LOOP/card.json`.
   - `passed: true` → go to step 6.
   - `passed: false` → note `flags`, go to step 5 (unless attempts exhausted).
5. **Rewrite (Opus, you):** if attempts remain,
   `python stage1_cli.py rewrite-prompt --body LOOP/body.md --instructions <flags-file> --title "<title>" --keyword "<keyword>" --affiliate-url "<affiliate_url>" --product-name "<name>" > LOOP/rwprompt.txt`,
   produce the corrected body, overwrite `LOOP/body.md`, and go back to step 3.
   If `MAX_ATTEMPTS` is hit without a pass → **hold**: do not stage; report the best
   scorecard and the blocking flags. (Phase 1: this is a data point, not a failure.)
6. **Stage:** `python stage1_cli.py stage --slug <slug> --body LOOP/body.md --pin-desc "$(cat LOOP/pindesc.txt)"`.
   A non-zero exit means the content contract failed — treat as a hold.
7. **PR:** create a branch, `git add _posts/ _pin_queue/ assets/images/pins/`, commit,
   push, and open a PR titled `stage1: <title>`. In the PR body include: attempts
   used, final reviewer scores, and total tokens/cost if known. **Do not merge**
   (Phase 1). Stop.

## Report
Always end with: topic, outcome (staged-PR / held), attempts used, final scores,
and any blocking flags.
````

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/happypet-stage1/SKILL.md
git commit -m "docs(stage1): routine operating procedure (write/review/rewrite/PR loop)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Supervised dry run + cost measurement (decision gate)

**Files:** none (procedure + findings). This task PROVES the design and produces the data for the Phase-2 go/no-go.

- [ ] **Step 1: Run the routine on the historically-held roundup**

Follow `.claude/skills/happypet-stage1/SKILL.md` for `best-dog-cooling-mat` (or whatever `next-topic` returns). Keep `AUTO_MERGE=off`.

- [ ] **Step 2: Record the results**

Capture, in a comment on the resulting PR (or a scratch note): attempts used, per-attempt reviewer scores (target 4), whether the authoritative gate passed, and approximate tokens/cost.

- [ ] **Step 3: Repeat on 2 more topics**

Run two more (`next-topic` twice more, or pick a single_review + a roundup) to see variance. Record the same metrics.

- [ ] **Step 4: Director review + decision**

Present to the Director: the produced articles, whether 4 is reachable and at what attempt/cost, and a recommended enforced bar (3 vs 4) and `MAX_ATTEMPTS`. **This is the Phase-1 → Phase-2 gate.** Do not start Phase 2 (scheduling, auto-merge, cron, `generate.yml` retirement) until the Director signs off here.

- [ ] **Step 5: Update the spec with the evidence**

Record the decided bar, `MAX_ATTEMPTS`, and measured cost in the spec's §6 and §9, so Phase 2 planning starts from data.

---

## Self-Review (completed while writing)

- **Spec coverage:** internal loop (Tasks 5-6), reuse-the-plumbing (Tasks 2-4), authoritative gate ignoring reviewer `pass` (Task 1), staging identical to today (Task 4), prove-4-and-measure-cost (Task 7). The `auto_merge` toggle and explicit Stage-2 dispatch are Phase-2 concerns (documented as such); the SKILL hard-codes `AUTO_MERGE=off` and stops at PR, so Phase 1 never needs them. CI content-gate check is Phase 2.
- **Placeholders:** none — every code step shows complete code; the two doc tasks (6, 7) are inherently prose but fully specified.
- **Type consistency:** `select_next_topic` returns `(slug, product)` or `None` (Tasks 2, 5 agree); `build_writer_inputs` returns the `{system,user,title,keyword,fmt,species,affiliate_url}` dict consumed by `cmd_next_topic` (Tasks 3, 5 agree); `stage_article` returns `{draft_path, pin_queue_path}` consumed by `cmd_stage` (Tasks 4, 5 agree); `authoritative_gate` returns `(passed, flags)` consumed by `cmd_gate` (Tasks 1, 5 agree).
- **Windows:** all file I/O uses `encoding="utf-8"`; tests use the venv interpreter.
```
