# Review/Rewrite Reliability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the generator/reviewer/rewrite loop in `generate_posts.py` from reliably failing articles on em dashes and first-person voice, without lowering the quality bar those two rules enforce.

**Architecture:** Two root causes were found by diffing the actual prompts sent to the model against the rules those same prompts declare, plus git-blaming the rule additions. Both root causes are self-contradictions baked into the prompt templates themselves — not model unreliability. The fix layers four defenses: (1) remove the contradictions from the prompts so the model isn't primed to break its own rules, (2) restate the two hardest-fail rules in the rewrite prompt (currently absent there), (3) verify em-dash/first-person violations against the actual article text in code instead of trusting the reviewer's self-report/judgment alone, (4) add one deterministic mechanical fallback that salvages an article which fails on em dashes alone after every rewrite attempt, instead of holding it. Nothing here weakens the pass criteria in `make_review_prompt()` — every fix either prevents the violation from being generated in the first place, or makes the existing hard-fail enforcement more reliable than "trust the LLM."

**Tech Stack:** Python 3.14, `generate_posts.py` (Gemini 2.5 Flash generator, Claude Haiku 4.5 reviewer via OpenRouter), `unittest` (`test_pipeline.py`), local venv at `.venv/Scripts/python.exe`.

---

## Evidence (read before starting)

On 2026-07-09 a local Stage 1 test run (topic `best-dog-cooling-mat`) failed review 3/3 attempts and was correctly held. Root cause, confirmed by reading the actual prompt strings sent to the model:

1. **`make_prompt()`'s own STRUCTURE templates use em dashes as a stylistic separator** — e.g. `generate_posts.py:826` `"Opening (100+ words, NO heading — begin prose directly)"` — in the *same prompt* that says three lines later (`generate_posts.py:870`) `"NEVER use em dashes (—)."` The model is shown ~8 em dashes as normal instructional style, then told never to use one. This primes exactly the failure we saw (12, then 12, then 8 em dashes across 3 rewrite attempts).

2. **`make_prompt()`'s opening "Good examples" are written in first person** — `generate_posts.py:876`: `"My dog chewed through a couch cushion..."` / `"I spent $40 on a toy..."` — three lines before `generate_posts.py:879`: `"Never use first-person voice (I, we, us, our, my)."` `git log -S` shows these two lines were added by *different commits*: the first-person examples in `2d14bd6` (Apr 8), the explicit ban in `d2a70a1` (May 18) — whose own commit message says `"M3 first-person voice contradiction"`, meaning a prior session already identified this exact contradiction and added the ban rule, but never fixed the examples that still violate it. Today's held article's first-person violations ("I noticed my own dog," "I laid it out") directly mirror the contradictory examples.

3. **`make_rewrite_prompt()` (used on every rewrite attempt) never restates either hard rule** — it relies entirely on the reviewer's dynamic `EDITOR FEEDBACK` text to carry "remove em dashes" / "remove first person" forward each attempt.

4. **First-person voice has no code-level enforcement.** `review_and_rewrite()` (`generate_posts.py:1100-1104`) already has a hard override that forces `pass=False` when `em_dash_count > 0`, independent of what the reviewer's `pass` field says. No equivalent exists for first-person — it depends entirely on the reviewer catching it.

5. **Two other format branches in `make_prompt()` have the same em-dash and first-person leaks** (`single_review`: `"What We Like"`, `"**Our Rating: X/5**"`; the buying-guide branch: `"Our Top Pick {product_name}"`, plus 2 more em dashes). These formats are currently unreachable — `refill_products.py:516` hardcodes every new queue entry to `"format": "roundup"` — but they're live code, worth fixing while already in the file.

---

## Task 1: Remove em dashes from `make_prompt()`'s own instruction templates

**Files:**
- Modify: `generate_posts.py:806` (single_review structure), `generate_posts.py:819` (verified_block), `generate_posts.py:826-857` (roundup + buying-guide structures)
- Test: `test_pipeline.py` (new `TestPromptHygiene` class)

- [ ] **Step 1: Write the failing test**

Add to `test_pipeline.py`, after the `TestReviewerResponseParsing` class (after line 288):

```python
class TestPromptHygiene(unittest.TestCase):
    """The generator's own prompt templates must not contain the characters
    or voice they forbid the model from using -- a prompt that says 'never
    use em dashes' while itself using em dashes as a separator primes the
    model to do exactly what it's told not to do."""

    SAMPLE_PRODUCT = {
        "name": "EHEYCIGA Cooling Mat for Dogs",
        "affiliate_url": "https://amzn.to/4cuvtEY",
        "stars": 4.8,
        "review_count": "1205",
        "price": "34.99",
    }

    def _prompt_for(self, fmt: str) -> str:
        import generate_posts as gp
        return gp.make_prompt(
            "Best Dog Cooling Mats", "best dog cooling mat", "best-dog-cooling-mat",
            fmt, self.SAMPLE_PRODUCT, "", "")

    def test_roundup_prompt_has_no_em_dashes(self):
        self.assertNotIn("—", self._prompt_for("roundup"))

    def test_single_review_prompt_has_no_em_dashes(self):
        self.assertNotIn("—", self._prompt_for("single_review"))

    def test_buying_guide_prompt_has_no_em_dashes(self):
        self.assertNotIn("—", self._prompt_for("buying_guide"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestPromptHygiene -v`
Expected: 3 failures, each `AssertionError: '—' unexpectedly found` (roundup fails first since it has the most em dashes).

- [ ] **Step 3: Fix the single_review structure (`generate_posts.py:806`)**

Old:
```python
        structure = f"""ARTICLE FORMAT: In-depth single product review of {product_name}
STRUCTURE: Opening (100+ words) | Product Overview (H2) | What We Like (H2, 4-5 features) | What Could Be Better (H2, 2-3 honest drawbacks) | Real Owner Experiences (H2) | Who Should Buy This (H2) | Verdict (H2, 80+ words with affiliate link) | Star rating: **Our Rating: X/5**"""
```

New (em dashes only here — none present, so unchanged by this step; see Task 2 for the first-person fix on this same line):
```python
        structure = f"""ARTICLE FORMAT: In-depth single product review of {product_name}
STRUCTURE: Opening (100+ words) | Product Overview (H2) | What We Like (H2, 4-5 features) | What Could Be Better (H2, 2-3 honest drawbacks) | Real Owner Experiences (H2) | Who Should Buy This (H2) | Verdict (H2, 80+ words with affiliate link) | Star rating: **Our Rating: X/5**"""
```

(No change needed in this step — `single_review` has no em dashes, only the first-person leaks handled in Task 2. Skip to Step 4.)

- [ ] **Step 4: Fix the verified_block and roundup structure (`generate_posts.py:808-854`)**

Old:
```python
    elif fmt == "roundup":
        # Build verified data block — only include fields we actually have
        verified_data = ""
        if stars:
            verified_data += f"  - Star rating : {stars}/5 (verified from Amazon)\n"
        if review_count:
            verified_data += f"  - Review count: {int(review_count):,} Amazon reviews\n"
        if price:
            verified_data += f"  - Price        : ${price} (verified from Amazon)\n"
        verified_block = ""
        if verified_data:
            verified_block = (
                f"VERIFIED PRODUCT DATA (use exactly as shown — do not alter or invent):\n"
                f"{verified_data}"
            )
        structure = f"""ARTICLE FORMAT: Roundup/comparison -- {title}

{verified_block}
STRUCTURE:
  Opening (100+ words, NO heading — begin prose directly)
  Quick Picks (H2)

  Featured Pick: {product_name} (H3, 150-200 words)
    - Reference the verified star rating and review count naturally in prose if available
    - Pros bullet list: 3-4 genuine strengths
    - Cons bullet list: 1-2 honest limitations
    - Include affiliate link per LINKING RULE above
    - Do not fabricate specs; hedge unverified claims ("many owners report..." / "tends to...")
    - NO invented personal stories, named dogs, specific dates, or fabricated test metrics

  Additional Picks: Use ONLY these real products from web search (H3 each, 60-75 words)
    {{ALTERNATIVE_PRODUCTS}}
    - Write each as a single prose paragraph — NO bullet lists
    - Naturally include 1-2 genuine strengths AND 1-2 honest limitations
    - DO NOT include star ratings, prices, specific specs, or fabricated statistics/percentages you cannot verify -- omit numbers entirely
    - Hedge unverified claims: "tends to...", "most owners find...", "works well for..."
    - DO NOT fabricate review data like "88% of owners reported..." -- if you don't have the number, don't include one
    - Use ONLY the products listed above — do not add or invent others
    - NO links for additional picks unless a URL is explicitly provided in the prompt

  Buying Guide (H2, 150+ words)

  Comparison Table (H2): Product | Best For | Price Range | Key Attribute
    - Price Range: use $, $$, $$$ only — do not invent specific dollar amounts for additional picks
    - Key Attribute: choose the most relevant column header for this product category (e.g. Form, CFU Count, Flavor, Size). Never use "Chew Time" for non-consumable products.
    - Do NOT include a ratings column — only use verified ratings from product data above

  Closing (80+ words with affiliate link per LINKING RULE above, NO heading — begin prose directly)"""
```

New:
```python
    elif fmt == "roundup":
        # Build verified data block -- only include fields we actually have
        verified_data = ""
        if stars:
            verified_data += f"  - Star rating : {stars}/5 (verified from Amazon)\n"
        if review_count:
            verified_data += f"  - Review count: {int(review_count):,} Amazon reviews\n"
        if price:
            verified_data += f"  - Price        : ${price} (verified from Amazon)\n"
        verified_block = ""
        if verified_data:
            verified_block = (
                f"VERIFIED PRODUCT DATA (use exactly as shown, do not alter or invent):\n"
                f"{verified_data}"
            )
        structure = f"""ARTICLE FORMAT: Roundup/comparison -- {title}

{verified_block}
STRUCTURE:
  Opening (100+ words, no heading: begin prose directly)
  Quick Picks (H2)

  Featured Pick: {product_name} (H3, 150-200 words)
    - Reference the verified star rating and review count naturally in prose if available
    - Pros bullet list: 3-4 genuine strengths
    - Cons bullet list: 1-2 honest limitations
    - Include affiliate link per LINKING RULE above
    - Do not fabricate specs; hedge unverified claims ("many owners report..." / "tends to...")
    - NO invented personal stories, named dogs, specific dates, or fabricated test metrics

  Additional Picks: Use ONLY these real products from web search (H3 each, 60-75 words)
    {{ALTERNATIVE_PRODUCTS}}
    - Write each as a single prose paragraph. No bullet lists.
    - Naturally include 1-2 genuine strengths AND 1-2 honest limitations
    - DO NOT include star ratings, prices, specific specs, or fabricated statistics/percentages you cannot verify -- omit numbers entirely
    - Hedge unverified claims: "tends to...", "most owners find...", "works well for..."
    - DO NOT fabricate review data like "88% of owners reported..." -- if you don't have the number, don't include one
    - Use ONLY the products listed above. Do not add or invent others.
    - NO links for additional picks unless a URL is explicitly provided in the prompt

  Buying Guide (H2, 150+ words)

  Comparison Table (H2): Product | Best For | Price Range | Key Attribute
    - Price Range: use $, $$, $$$ only. Do not invent specific dollar amounts for additional picks.
    - Key Attribute: choose the most relevant column header for this product category (e.g. Form, CFU Count, Flavor, Size). Never use "Chew Time" for non-consumable products.
    - Do NOT include a ratings column. Only use verified ratings from product data above.

  Closing (80+ words with affiliate link per LINKING RULE above, no heading: begin prose directly)"""
```

- [ ] **Step 5: Fix the buying-guide structure (`generate_posts.py:856-857`)**

Old:
```python
    else:
        structure = f"""ARTICLE FORMAT: Buying guide -- {title}
STRUCTURE: Opening (100+ words, NO heading — begin prose directly) | What to Look For (H2, 5-6 key factors) | Our Top Pick {product_name} (H2, 100 words, affiliate link) | Common Mistakes to Avoid (H2, 3-4 pitfalls) | FAQ (H2, 4-5 real questions) | Closing (80+ words with affiliate link, NO heading — begin prose directly)"""
```

New (em-dash fix only here -- the "Our Top Pick" first-person fix is Task 2):
```python
    else:
        structure = f"""ARTICLE FORMAT: Buying guide -- {title}
STRUCTURE: Opening (100+ words, no heading: begin prose directly) | What to Look For (H2, 5-6 key factors) | Our Top Pick {product_name} (H2, 100 words, affiliate link) | Common Mistakes to Avoid (H2, 3-4 pitfalls) | FAQ (H2, 4-5 real questions) | Closing (80+ words with affiliate link, no heading: begin prose directly)"""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestPromptHygiene -v`
Expected: `test_roundup_prompt_has_no_em_dashes` and `test_buying_guide_prompt_has_no_em_dashes` now PASS. `test_single_review_prompt_has_no_em_dashes` already passed (no em dashes existed there) and still passes.

- [ ] **Step 7: Run the full suite to check nothing else broke**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: same pass count as baseline plus 3 new passes; the 2 pre-existing Windows cp1252 workflow-YAML failures are unrelated and expected.

- [ ] **Step 8: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "fix(generate): strip em dashes from the generator's own prompt templates

make_prompt()'s STRUCTURE and verified-data-block text used em dashes as a
stylistic separator in the same prompt that tells the model NEVER to use
one -- priming exactly the violation it forbids. Confirmed as the likely
root cause after a real held article came back with 12/12/8 em dashes
across 3 rewrite attempts despite the explicit ban."
```

---

## Task 2: Fix first-person leaks in `make_prompt()`

**Files:**
- Modify: `generate_posts.py:806` (single_review), `generate_posts.py:857` (buying guide, from Task 1's new text), `generate_posts.py:876` (opening "Good examples")
- Test: `test_pipeline.py` (extend `TestPromptHygiene`)

- [ ] **Step 1: Write the failing test**

Add to `TestPromptHygiene` (after the em-dash tests from Task 1):

```python
    FIRST_PERSON_RE = staticmethod(__import__("re").compile(r"\b(I|we|us|our|my)\b", __import__("re").IGNORECASE))

    def _first_person_hits(self, text: str) -> list:
        return self.FIRST_PERSON_RE.findall(text)

    def test_roundup_prompt_has_no_first_person(self):
        prompt = self._prompt_for("roundup")
        # The rule statement itself legitimately names the banned words
        # ("Never use first-person voice (I, we, us, our, my)") -- exclude
        # that one line, check everything else.
        body = "\n".join(
            line for line in prompt.splitlines()
            if "Never use first-person voice" not in line
        )
        hits = self._first_person_hits(body)
        self.assertEqual(hits, [], f"Unexpected first-person words in prompt: {hits}")

    def test_single_review_prompt_has_no_first_person(self):
        hits = self._first_person_hits(self._prompt_for("single_review"))
        self.assertEqual(hits, [], f"Unexpected first-person words in prompt: {hits}")

    def test_buying_guide_prompt_has_no_first_person(self):
        hits = self._first_person_hits(self._prompt_for("buying_guide"))
        self.assertEqual(hits, [], f"Unexpected first-person words in prompt: {hits}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestPromptHygiene -v`
Expected: the 3 new tests FAIL. `test_roundup_prompt_has_no_first_person` reports hits from the "Good examples" opening line (`My dog`, `I spent`, `my dog`). `test_single_review_prompt_has_no_first_person` reports `We` (from "What We Like") and `Our` (from "**Our Rating: X/5**"). `test_buying_guide_prompt_has_no_first_person` reports `Our` (from "Our Top Pick").

- [ ] **Step 3: Fix the single_review structure (`generate_posts.py:806`)**

Old:
```python
        structure = f"""ARTICLE FORMAT: In-depth single product review of {product_name}
STRUCTURE: Opening (100+ words) | Product Overview (H2) | What We Like (H2, 4-5 features) | What Could Be Better (H2, 2-3 honest drawbacks) | Real Owner Experiences (H2) | Who Should Buy This (H2) | Verdict (H2, 80+ words with affiliate link) | Star rating: **Our Rating: X/5**"""
```

New:
```python
        structure = f"""ARTICLE FORMAT: In-depth single product review of {product_name}
STRUCTURE: Opening (100+ words) | Product Overview (H2) | What Stood Out (H2, 4-5 features) | What Could Be Better (H2, 2-3 honest drawbacks) | Real Owner Experiences (H2) | Who Should Buy This (H2) | Verdict (H2, 80+ words with affiliate link) | Star rating: **Rating: X/5**"""
```

- [ ] **Step 4: Fix the buying-guide structure (`generate_posts.py:857`, as left by Task 1)**

Old:
```python
STRUCTURE: Opening (100+ words, no heading: begin prose directly) | What to Look For (H2, 5-6 key factors) | Our Top Pick {product_name} (H2, 100 words, affiliate link) | Common Mistakes to Avoid (H2, 3-4 pitfalls) | FAQ (H2, 4-5 real questions) | Closing (80+ words with affiliate link, no heading: begin prose directly)"""
```

New:
```python
STRUCTURE: Opening (100+ words, no heading: begin prose directly) | What to Look For (H2, 5-6 key factors) | Top Pick: {product_name} (H2, 100 words, affiliate link) | Common Mistakes to Avoid (H2, 3-4 pitfalls) | FAQ (H2, 4-5 real questions) | Closing (80+ words with affiliate link, no heading: begin prose directly)"""
```

- [ ] **Step 5: Fix the contradictory opening "Good examples" (`generate_posts.py:876`)**

Old:
```python
    return f"""You are a senior writer for Happy Pet Product Reviews, a trusted budget-focused pet product review blog.
...
- OPENING: If it makes sense for the article topic, open with a specific relatable moment a dog or cat owner would instantly recognize. Show, don't tell. Be SPECIFIC -- name a real scenario, not a generic one.
  Good examples: "My dog chewed through a couch cushion on a 45-minute Zoom call." / "Our cat knocked the water bowl over three times in one week." / "I spent $40 on a toy my dog sniffed once and walked away from."
  Bad examples (NEVER write openings like these): "We've all been there - [generic scenario]..." (cliché opener) / "As a pet owner, you know how important it is to..." (filler) / "Dogs need mental stimulation to stay happy and healthy." (generic) / "Standing in the kitchen when suddenly..." (AI-template setup) / Any opening that starts with a vague scenario followed by a product pitch.
  If the article topic is purely practical (e.g. flea prevention, nutrition), a direct factual opening is fine -- do not force an anecdote.
- Use "{keyword}" naturally 4-6 times. Write in second person ("your dog", "you'll find") or third person ("owners report", "dogs tend to"). Never use first-person voice (I, we, us, our, my) -- the reviewer will fail any article that does.{link}
```

New (examples rewritten to the same specificity in second/third person, matching the rule two lines below them instead of contradicting it):
```python
    return f"""You are a senior writer for Happy Pet Product Reviews, a trusted budget-focused pet product review blog.
...
- OPENING: If it makes sense for the article topic, open with a specific relatable moment a dog or cat owner would instantly recognize. Show, don't tell. Be SPECIFIC -- name a real scenario, not a generic one.
  Good examples: "A couch cushion doesn't stand a chance against a bored dog during a 45-minute Zoom call." / "A knocked-over water bowl three times in one week is a familiar mess for a lot of cat owners." / "Forty dollars for a toy a dog sniffs once and walks away from is a familiar kind of frustrating."
  Bad examples (NEVER write openings like these): "We've all been there - [generic scenario]..." (cliché opener) / "As a pet owner, you know how important it is to..." (filler) / "Dogs need mental stimulation to stay happy and healthy." (generic) / "Standing in the kitchen when suddenly..." (AI-template setup) / Any opening that starts with a vague scenario followed by a product pitch.
  If the article topic is purely practical (e.g. flea prevention, nutrition), a direct factual opening is fine -- do not force an anecdote.
- Use "{keyword}" naturally 4-6 times. Write in second person ("your dog", "you'll find") or third person ("owners report", "dogs tend to"). Never use first-person voice (I, we, us, our, my) -- the reviewer will fail any article that does.{link}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestPromptHygiene -v`
Expected: all 6 `TestPromptHygiene` tests PASS.

- [ ] **Step 7: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: same as Task 1's baseline plus these 3 additional passes.

- [ ] **Step 8: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "fix(generate): remove first-person leaks from make_prompt()'s own templates

The opening 'Good examples' (My dog chewed..., I spent \$40...) directly
contradicted the 'never use first-person voice' rule three lines below
them. git log -S shows the ban rule was added in d2a70a1 -- whose own
commit message says 'M3 first-person voice contradiction' -- without ever
fixing the examples that still violate it. Also fixed two dead-but-live
leaks in the single_review and buying-guide structure headings (What We
Like / Our Rating / Our Top Pick) so they don't reintroduce the same
contradiction if those formats are ever used."
```

---

## Task 3: Restate the hard rules in `make_rewrite_prompt()`

**Files:**
- Modify: `generate_posts.py:745-778` (`make_rewrite_prompt`)
- Test: `test_pipeline.py` (new `TestRewritePromptRules` class)

- [ ] **Step 1: Write the failing test**

Add to `test_pipeline.py`, after `TestPromptHygiene`:

```python
class TestRewritePromptRules(unittest.TestCase):
    """make_rewrite_prompt() runs on every review-failure retry. The two
    hardest-fail rules (no em dashes, no first person) must be restated as
    standing rules in the template itself, not left to whatever the
    reviewer's dynamic EDITOR FEEDBACK text happens to say that attempt."""

    def test_rewrite_prompt_restates_em_dash_rule(self):
        import generate_posts as gp
        prompt = gp.make_rewrite_prompt(
            "Best Dog Cooling Mats", "best dog cooling mat", "Some article body.",
            "Fix the pacing in paragraph 2.")
        self.assertIn("em dash", prompt.lower())

    def test_rewrite_prompt_restates_first_person_rule(self):
        import generate_posts as gp
        prompt = gp.make_rewrite_prompt(
            "Best Dog Cooling Mats", "best dog cooling mat", "Some article body.",
            "Fix the pacing in paragraph 2.")
        self.assertIn("first-person", prompt.lower())

    def test_rewrite_prompt_rules_present_even_with_unrelated_feedback(self):
        # The whole point: these rules must survive regardless of what the
        # reviewer's rewrite_instructions happened to focus on this attempt.
        import generate_posts as gp
        prompt = gp.make_rewrite_prompt(
            "Best Dog Cooling Mats", "best dog cooling mat", "Some article body.",
            "Tighten the buying guide section, it's too vague.")
        self.assertIn("em dash", prompt.lower())
        self.assertIn("first-person", prompt.lower())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestRewritePromptRules -v`
Expected: all 3 FAIL — the current `make_rewrite_prompt()` never mentions em dashes or first-person at all.

- [ ] **Step 3: Add the two rules to `make_rewrite_prompt()`'s REWRITE RULES block**

Old (`generate_posts.py:764-774`):
```python
REWRITE RULES:
- Fix exactly what the editor flagged. Do not rewrite sections that passed.
- Where the editor flagged generic or AI-patterned writing, replace with something SPECIFIC.
  A specific detail beats a fluent generality every time.
  BAD: "Many cat owners find this litter box easy to clean."
  GOOD: "The front-entry design means you scoop from the top instead of kneeling on the floor."
- Where warmth is flagged, add a concrete human moment -- a scenario, a frustration, a small observation.
  Do not add hollow affirmations ("Great news!", "You'll love..."). Real warmth is specific.
- Where transitions feel templated, cut them or rewrite as a direct statement.
  Never use: "Overall", "In summary", "Whether you", "At the end of the day", "Ultimately".
- Read your output. If any sentence could have been written by a content farm, rewrite it.
```

New:
```python
REWRITE RULES:
- Fix exactly what the editor flagged. Do not rewrite sections that passed.
- NEVER use em dashes (—) anywhere in the rewrite, even in sections you are not
  otherwise touching. If the original text contains one, remove it as part of
  this pass -- rewrite the sentence, don't just swap in a different dash.
- NEVER use first-person voice (I, we, us, our, my) anywhere in the rewrite,
  even in sections you are not otherwise touching. Use second person ("your
  dog") or third person ("owners report") instead.
- Where the editor flagged generic or AI-patterned writing, replace with something SPECIFIC.
  A specific detail beats a fluent generality every time.
  BAD: "Many cat owners find this litter box easy to clean."
  GOOD: "The front-entry design means you scoop from the top instead of kneeling on the floor."
- Where warmth is flagged, add a concrete human moment -- a scenario, a frustration, a small observation.
  Do not add hollow affirmations ("Great news!", "You'll love..."). Real warmth is specific.
- Where transitions feel templated, cut them or rewrite as a direct statement.
  Never use: "Overall", "In summary", "Whether you", "At the end of the day", "Ultimately".
- Read your output. If any sentence could have been written by a content farm, rewrite it.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestRewritePromptRules -v`
Expected: all 3 PASS.

- [ ] **Step 5: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: baseline plus 3 new passes.

- [ ] **Step 6: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "fix(generate): restate em-dash/first-person hard rules in the rewrite prompt

make_rewrite_prompt() runs on every review-failure retry but never
mentioned either hard-fail rule itself -- it relied entirely on the
reviewer's dynamic EDITOR FEEDBACK text to carry the constraint forward
each attempt. Now both rules are standing instructions in the template,
independent of what that attempt's feedback happened to focus on."
```

---

## Task 4: Verify em-dash/first-person against the actual text, not just the reviewer's self-report

**Files:**
- Modify: `generate_posts.py:1053-1170` (`review_and_rewrite`, the two hard-override blocks around line 1100)
- Test: `test_pipeline.py` (extend `TestReviewerResponseParsing`)

- [ ] **Step 1: Write the failing test**

Add to `TestReviewerResponseParsing` (after `test_em_dash_fail_overrides_pass_true`, currently ending at line 288):

```python
    def test_em_dash_override_catches_under_reported_count(self):
        """If the reviewer under-reports em_dash_count (says 0 while the
        article still has one), code-level ground truth must still catch it."""
        import generate_posts as gp
        scorecard = json.loads(REVIEWER_PASS_JSON)
        scorecard["em_dash_count"] = 0  # reviewer missed it
        content = "This mat is soft — and dries fast."
        # Replicate the hardened override logic from review_and_rewrite
        actual_em_dashes = content.count("—")
        em_dashes = max(scorecard.get("em_dash_count", 0), actual_em_dashes)
        passed = scorecard["pass"]
        if passed and em_dashes > 0:
            passed = False
        self.assertFalse(passed)
        self.assertEqual(em_dashes, 1)

    def test_first_person_override_forces_fail_even_if_reviewer_missed_it(self):
        """First-person voice must be code-enforced the same way em dashes
        already are -- not left entirely to the reviewer's judgment."""
        import generate_posts as gp
        scorecard = json.loads(REVIEWER_PASS_JSON)  # reviewer said pass=true
        content = "I really like how quiet this mat is."
        passed = scorecard["pass"]
        first_person_hit = gp.FIRST_PERSON_RE.search(content)
        if passed and first_person_hit:
            passed = False
        self.assertFalse(passed)
        self.assertEqual(first_person_hit.group(0), "I")

    def test_first_person_override_ignores_clean_text(self):
        import generate_posts as gp
        content = "Your dog will appreciate the cooling fabric on hot days."
        self.assertIsNone(gp.FIRST_PERSON_RE.search(content))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestReviewerResponseParsing -v`
Expected: `test_em_dash_override_catches_under_reported_count` PASSES already (it only replicates logic inline, doesn't touch new code). `test_first_person_override_forces_fail_even_if_reviewer_missed_it` and `test_first_person_override_ignores_clean_text` FAIL with `AttributeError: module 'generate_posts' has no attribute 'FIRST_PERSON_RE'`.

- [ ] **Step 3: Add the `FIRST_PERSON_RE` constant**

Add directly above `def review_and_rewrite(` (currently `generate_posts.py:1053`):

```python
# Mirrors the hard-fail rule stated in make_prompt()/make_rewrite_prompt()/
# make_review_prompt(). Word-boundary + case-insensitive so it catches
# sentence-start "We"/"I" too, without matching inside other words.
FIRST_PERSON_RE = re.compile(r"\b(I|we|us|our|my)\b", re.IGNORECASE)


def review_and_rewrite(title: str, keyword: str, content: str, api_key: str, or_key: str = "", affiliate_url: str = "", product_name: str = "") -> tuple:
```

(This replaces the existing `def review_and_rewrite(...)` line -- keep everything else in the function body unchanged for this step.)

- [ ] **Step 4: Harden the em-dash override and add the first-person override**

Old (`generate_posts.py:1100-1104`):
```python
        # Hard override: em dash count > 0 always fails
        if passed and em_dashes > 0:
            log_reviewer(f"  OVERRIDE: pass forced to FAIL -- {em_dashes} em dash(es) found", "WARN")
            passed = False
            flags = flags + [f"em_dash_count={em_dashes}"]
```

New:
```python
        # Hard override: em dash count > 0 always fails. Don't just trust the
        # reviewer's self-reported count -- count the actual article text too,
        # so a reviewer that under-reports (says 0 while dashes remain) can't
        # let one slip through.
        actual_em_dashes = content.count("—")
        if actual_em_dashes > em_dashes:
            em_dashes = actual_em_dashes
        if passed and em_dashes > 0:
            log_reviewer(f"  OVERRIDE: pass forced to FAIL -- {em_dashes} em dash(es) found "
                         f"(reviewer reported {scorecard.get('em_dash_count', 0)}, code-counted {actual_em_dashes})", "WARN")
            passed = False
            flags = flags + [f"em_dash_count={em_dashes}"]
        # Hard override: first-person author voice always fails, mirroring
        # the em-dash override above. Previously this rule existed only in
        # the reviewer's own prompt/judgment with no code-level backstop.
        first_person_hit = FIRST_PERSON_RE.search(content)
        if passed and first_person_hit:
            log_reviewer(f"  OVERRIDE: pass forced to FAIL -- first-person voice found: "
                         f"{first_person_hit.group(0)!r}", "WARN")
            passed = False
            flags = flags + [f"first_person_detected={first_person_hit.group(0)!r}"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k "TestReviewerResponseParsing or TestPromptHygiene or TestRewritePromptRules" -v`
Expected: all PASS, including the 3 new tests from this task.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: baseline plus all new tests from Tasks 1-4.

- [ ] **Step 7: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "fix(generate): code-verify em-dash/first-person instead of trusting the reviewer alone

The em-dash hard override trusted the reviewer's self-reported
em_dash_count; now it's compared against an actual regex count of the
article text and the higher of the two wins. First-person voice had NO
code-level override at all -- it depended entirely on the reviewer
catching it in its own judgment. Added FIRST_PERSON_RE and a matching
hard override, symmetric with the existing em-dash one."
```

---

## Task 5: Deterministic mechanical fallback for em-dash-only failures

**Files:**
- Modify: `generate_posts.py:1053-1170` (`review_and_rewrite`, the final-attempt failure branch)
- Test: `test_pipeline.py` (new `TestEmDashBackstop` class)

**Why:** Tasks 1-4 reduce how often em dashes appear and make detection more reliable, but a model can still emit one on the final rewrite attempt. Right now that means the article is held and a GitHub issue filed -- even when em dashes are the *only* thing wrong and every other pass criterion (voice, warmth, readability, accuracy, affiliate link) already cleared. An em dash is the one failure mode on this list that's mechanically, safely fixable without another expensive LLM round-trip: splitting `"X — Y"` into `"X. Y"` doesn't change meaning or tone, so it can't invalidate the human_voice/warmth/readability scores the reviewer already gave this exact text.

- [ ] **Step 1: Write the failing test for the pure helper function**

Add to `test_pipeline.py`, after `TestRewritePromptRules`:

```python
class TestEmDashBackstop(unittest.TestCase):
    """strip_em_dashes(): last-resort mechanical fix, only ever invoked when
    em dashes are the sole remaining reason an otherwise-passing article
    would be held."""

    def test_no_em_dash_is_a_noop(self):
        import generate_posts as gp
        text = "This mat stays cool for hours."
        self.assertEqual(gp.strip_em_dashes(text), text)

    def test_single_em_dash_splits_into_two_sentences(self):
        import generate_posts as gp
        result = gp.strip_em_dashes("This mat is soft — it also dries fast.")
        self.assertNotIn("—", result)
        self.assertEqual(result, "This mat is soft. It also dries fast.")

    def test_multiple_em_dashes_all_removed(self):
        import generate_posts as gp
        result = gp.strip_em_dashes("Great fit — easy to clean — dries fast.")
        self.assertNotIn("—", result)
        self.assertEqual(result, "Great fit. Easy to clean. Dries fast.")

    def test_em_dash_immediately_after_period_does_not_double_period(self):
        import generate_posts as gp
        result = gp.strip_em_dashes("Great color. — Also waterproof.")
        self.assertNotIn("—", result)
        self.assertNotIn("..", result)
        self.assertEqual(result, "Great color. Also waterproof.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestEmDashBackstop -v`
Expected: all 4 FAIL with `AttributeError: module 'generate_posts' has no attribute 'strip_em_dashes'`.

- [ ] **Step 3: Implement `strip_em_dashes()`**

Add directly above the `FIRST_PERSON_RE` constant added in Task 4 (`generate_posts.py`, just above `def review_and_rewrite(`):

```python
def strip_em_dashes(text: str) -> str:
    """Deterministically remove every em dash by splitting the clause into
    two sentences. Last-resort mechanical fix -- only ever called when an
    article has already cleared every other pass criterion and em dashes
    are the sole remaining blocker, so meaning/tone are not at stake, only
    punctuation."""
    if "—" not in text:
        return text
    result = re.sub(r"\s*—\s*", ". ", text)
    result = re.sub(r"\.\s*\.", ".", result)  # collapse "X.. Y" when a dash followed a period
    result = re.sub(r"\. ([a-z])", lambda m: ". " + m.group(1).upper(), result)
    return result
```

- [ ] **Step 4: Run tests to verify the helper passes**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestEmDashBackstop -v`
Expected: all 4 PASS.

- [ ] **Step 5: Write the failing test for wiring it into `review_and_rewrite`'s final-failure branch**

Add to `TestEmDashBackstop`:

```python
    def test_only_em_dash_blocked_helper_true_when_everything_else_passes(self):
        import generate_posts as gp
        scores = {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}
        self.assertTrue(gp._only_em_dash_blocked(scores, ["em_dash_count=1"], True))

    def test_only_em_dash_blocked_helper_false_with_other_flags(self):
        import generate_posts as gp
        scores = {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}
        self.assertFalse(gp._only_em_dash_blocked(
            scores, ["em_dash_count=1", "first_person_detected='I'"], True))

    def test_only_em_dash_blocked_helper_false_when_score_too_low(self):
        import generate_posts as gp
        scores = {"human_voice": 2, "warmth": 4, "readability": 4, "accuracy": 4}
        self.assertFalse(gp._only_em_dash_blocked(scores, ["em_dash_count=1"], True))

    def test_only_em_dash_blocked_helper_false_without_affiliate_link(self):
        import generate_posts as gp
        scores = {"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}
        self.assertFalse(gp._only_em_dash_blocked(scores, ["em_dash_count=1"], False))
```

- [ ] **Step 6: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestEmDashBackstop -v`
Expected: the 4 new tests FAIL with `AttributeError: module 'generate_posts' has no attribute '_only_em_dash_blocked'`.

- [ ] **Step 7: Implement `_only_em_dash_blocked()` and wire the backstop into the final-failure branch**

Add directly below `strip_em_dashes()`:

```python
def _only_em_dash_blocked(scores: dict, flags: list, affiliate_link_present: bool) -> bool:
    """True if every other pass criterion is met and em_dash_count is the
    sole remaining reason the article would fail."""
    non_em_dash_flags = [f for f in flags if not str(f).lower().startswith("em_dash_count")]
    return (
        affiliate_link_present
        and scores.get("human_voice", 0) >= 4
        and scores.get("warmth", 0) >= 4
        and scores.get("readability", 0) >= 3
        and scores.get("accuracy", 0) >= 3
        and not non_em_dash_flags
    )
```

Old (`generate_posts.py`, the final `else` branch inside `review_and_rewrite`'s attempt loop):
```python
        else:
            log_reviewer(f"  REVIEW FAILED after {attempt} attempt(s) -- creating GitHub issue", "WARN")
            return content, False, flags
```

New:
```python
        else:
            actual_em_dashes = content.count("—")
            if actual_em_dashes > 0 and _only_em_dash_blocked(
                scores, flags, scorecard.get("affiliate_link_present", False)
            ):
                cleaned = strip_em_dashes(content)
                log_reviewer(
                    f"  MECHANICAL FIX: stripped {actual_em_dashes} em dash(es) -- "
                    f"every other pass criterion already met on this draft, accepting "
                    f"without another rewrite round-trip"
                )
                return cleaned, True, []
            log_reviewer(f"  REVIEW FAILED after {attempt} attempt(s) -- creating GitHub issue", "WARN")
            return content, False, flags
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -k TestEmDashBackstop -v`
Expected: all 8 tests in this class PASS.

- [ ] **Step 9: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest test_pipeline.py -q`
Expected: baseline plus all new tests from Tasks 1-5. Same 2 pre-existing unrelated Windows cp1252 failures.

- [ ] **Step 10: Commit**

```bash
git add generate_posts.py test_pipeline.py
git commit -m "feat(generate): mechanically salvage articles that fail on em dashes alone

Added strip_em_dashes() (deterministic: split 'X -- Y' into two sentences)
and _only_em_dash_blocked() (true when every other pass criterion already
cleared). When the final rewrite attempt still has em dashes but nothing
else is wrong, the article is mechanically cleaned and accepted instead
of held -- the reviewer already validated this exact text's voice/warmth/
readability/accuracy, and a deterministic punctuation fix can't invalidate
that. Articles with any other problem still go through the normal
hold + GitHub issue path unchanged."
```

---

## Task 6: Empirical validation against the article that failed today

**Files:** none (validation run only, no code changes)

- [ ] **Step 1: Confirm the target topic is still unpublished**

Run: `./.venv/Scripts/python.exe -c "import json; d=json.load(open('products.json')); print([p['topic'] for p in d if p['topic']=='best-dog-cooling-mat'])"`
Expected: `['best-dog-cooling-mat']` -- still in the queue, untouched by this plan.

- [ ] **Step 2: Re-run the same local, git-untouched Stage 1 harness used for the original test**

Reuse the harness from the earlier session (loads `GEMINI_API_KEY`/`OPENROUTER_API_KEY` from Maeve's vault via `brain_secrets`, sets `MAX_ARTICLES=1`, strips `GH_TOKEN`/`GITHUB_TOKEN` from the subprocess env, does not commit or push). If the scratchpad copy no longer exists, recreate it:

```python
import os, subprocess, sys
REPO = r"C:\Users\derek\MAEVE\HappyPet"
sys.path.insert(0, REPO)
os.chdir(REPO)
import brain_secrets as bs
env = dict(os.environ)
for name in ("GEMINI_API_KEY", "OPENROUTER_API_KEY"):
    val = bs.get_secret(name)
    if not val:
        print(f"FATAL: {name} not found in vault -- aborting before any API calls.")
        sys.exit(1)
    env[name] = val
env["MAX_ARTICLES"] = "1"
env.pop("GH_TOKEN", None)
env.pop("GITHUB_TOKEN", None)
result = subprocess.run([r".venv\Scripts\python.exe", "generate_posts.py"], cwd=REPO, env=env)
print(f"exit {result.returncode}")
```

Run it with `./.venv/Scripts/python.exe <script path>`. This costs one real Gemini generation + up to 3 real Haiku review round-trips -- same as the original test, no larger.

Note: `create_github_issue()` may still fire for real if review fails 3/3 again and the local `gh` CLI has a persisted login (as it did in the original test run, filing #52). That's an accepted, disclosed side effect of this validation step, not a new risk introduced by this plan.

- [ ] **Step 3: Inspect the outcome**

Check `GENERATION_RESULT.json` and the console log. Compare against the original run's result (`0 written, 0 skipped, 1 held, 0 failed`, em-dash counts 12/12/8, first-person violations present).

Success looks like one of:
- `1 written, 0 held` with the reviewer passing outright, or
- `1 written, 0 held` with the Task 5 mechanical backstop firing (log line `MECHANICAL FIX: stripped N em dash(es)`) after em-dash was the only remaining blocker, or
- `0 written, 1 held` but with materially fewer em-dash/first-person violations than the original run, which would indicate partial improvement worth a second data point before concluding more work is needed.

- [ ] **Step 4: Verify nothing was published/pinned/synced**

Run: `git status --short`
Expected: only harmless untracked local files (`GENERATION_RESULT.json`, pre-existing `CLAUDE.md`), same as the original test -- no `_posts/DRAFT-*.md` committed, nothing pushed.

- [ ] **Step 5: Report the result**

No commit for this task -- it's a validation run. Summarize the before/after comparison for Derek: did the held article now pass, and if the mechanical backstop fired, confirm the final article reads correctly (spot-check the split sentences don't read awkwardly).

---

## Self-Review Notes

- **Spec coverage:** every root cause found in the review (em-dash priming, first-person priming, rewrite prompt silence on both rules, no code-level first-person enforcement, no mechanical recovery path) has a corresponding task. The two dead-but-live format branches (`single_review`, `buying_guide`) are fixed opportunistically in Tasks 1-2 since they're touched anyway and share the exact same bug pattern.
- **No placeholders:** every step has complete before/after code, exact file references, and exact test commands with expected output.
- **Type/name consistency:** `FIRST_PERSON_RE`, `strip_em_dashes`, and `_only_em_dash_blocked` are each defined once (Tasks 4-5) and referenced identically in every later step and test.
- **Criteria preserved, not weakened:** no task raises a threshold, removes a check, or disables the em-dash/first-person rules. Every task either prevents the violation from being generated, or makes existing hard-fail enforcement more reliable, or mechanically fixes a violation only after every *other* criterion already passed on that exact text.
