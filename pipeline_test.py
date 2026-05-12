#!/usr/bin/env python3
"""
pipeline_test.py -- HappyPet Stage 1 test harness
Tests generator + reviewer using identical logic to GHA pipeline.
No publishing, no pinning, no commits. Output only.

Usage:
  python3 pipeline_test.py
  python3 pipeline_test.py --topic "best-dog-puzzle-toys"
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
try:
    from brain_secrets import get_secret
    OR_KEY     = os.environ.get('OPENROUTER_API_KEY')  or get_secret('OPENROUTER_API_KEY')
    GEMINI_KEY = os.environ.get('GEMINI_API_KEY')      or get_secret('GEMINI_API_KEY')
except Exception:
    OR_KEY     = os.environ.get('OPENROUTER_API_KEY', '')
    GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')

OPENROUTER_URL   = 'https://openrouter.ai/api/v1/chat/completions'
OR_GEN_MODEL     = 'openai/gpt-oss-120b:free'
OR_GEN_FALLBACK  = 'gemini-2.5-flash-lite'   # Gemini direct
REVIEWER_MODEL   = 'gemini-2.5-flash-lite'   # Gemini direct
REVIEWER_FALLBACK= 'openai/gpt-oss-20b:free' # OR fallback
OR_HEADERS       = {'HTTP-Referer': 'https://happypetproductreviews.com', 'X-Title': 'HappyPetTest'}

# Test product -- use a real entry format matching products.json
TEST_PRODUCT = {
    'topic':   'best-dog-puzzle-toys',
    'title':   'Best Dog Puzzle Toys to Keep Smart Dogs Busy and Engaged',
    'keyword': 'best dog puzzle toys',
    'name':    'Outward Hound Hide-A-Squirrel Dog Toy, 2-in-1 Plush Puzzle, 6 Squeaky Squirrels, XL',
    'asin':    'B005VS9WO6',
    'affiliate_url': 'https://amzn.to/4sAXxfA',
    'species': 'dog',
    'category':'dog-gear',
    'format':  'roundup',
    'stars':   4.6,
    'price':   '29.69',
    'runners_up': 'PETSTA Treat Dispensing Puzzle; Forfon 9-Pack Puzzle Set; Outward Hound Dog Tornado',
}


def log(msg, level='INFO'):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] [{level}] {msg}', flush=True)


def http_post(url, payload, headers, label='', timeout=90, retries=2, backoff_base=30):
    last_exc = None
    for attempt in range(1, retries + 2):
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:200]
            last_exc = f'HTTP {e.code}: {body}'
            if e.code == 429 and attempt <= retries:
                wait = backoff_base * attempt
                log(f'  {label} 429 -- waiting {wait}s', 'WARN')
                time.sleep(wait)
            else:
                raise RuntimeError(f'{label} {last_exc}')
        except Exception as e:
            last_exc = str(e)
            if attempt <= retries:
                time.sleep(backoff_base)
            else:
                raise RuntimeError(f'{label} exhausted: {last_exc}')


def make_prompt(product: dict) -> str:
    title   = product['title']
    keyword = product['keyword']
    name    = product['name']
    stars   = product.get('stars', '')
    price   = product.get('price', '')
    runners = product.get('runners_up', '')
    affiliate_url = product.get('affiliate_url', '')

    affiliate_block = ''
    if affiliate_url:
        affiliate_block = f"""
FEATURED PRODUCT DATA (verified -- use these exact figures):
- Product: {name}
- Amazon rating: {stars}/5 stars
- Price: ${price}
- Affiliate link: {affiliate_url}

LINKING RULE: Include the affiliate link as a natural markdown link in the Featured Pick section AND once in the closing. Nowhere else.

ALTERNATIVES: Include 3 alternatives from this list (use ONLY these, do not invent others): {runners}
For each alternative: one specific, verifiable detail that distinguishes it. No invented specs, no fabricated ratings."""

    structure = f"""ARTICLE FORMAT: Roundup -- {title}
STRUCTURE:
  Opening (100+ words, NO heading -- begin prose directly)
  ## The Best {keyword.title()} at a Glance (H2 -- comparison table with columns: Product | Best For | Price Range | Key Feature)
  ## Our Top Pick: {name} (H2, 150+ words, affiliate link, pros/cons list)
  ### Additional Picks (H3 -- 3 alternatives, 60-80 words each, no affiliate links)
  ## Buying Guide (H2 -- 4-5 factors, 200+ words)
  ## Comparison Table (H2 -- same products as glance table, add Durability or Chew Time column for consumables)
  Closing (80+ words with affiliate link, NO heading -- begin prose directly)"""

    return f"""You are a senior writer for Happy Pet Product Reviews, a trusted budget-focused pet product review blog.

Write a complete, publish-ready blog post. Title: "{title}". Focus keyword: "{keyword}".
{affiliate_block}

LENGTH: 950-1100 words of body content. Firm requirement. Complete ALL sections before stopping.

{structure}

WRITING STYLE:
- Conversational, warm, authoritative -- like advice from a trusted friend who owns pets
- Vary sentence length. Short punchy sentences mixed with longer flowing ones.
- Use hyphens (-) for compound words. NEVER use em dashes (--). Rewrite the sentence instead.
- MINIMIZE stock phrases: never use delve, game-changer, comprehensive guide, furry friend, fur baby, pet parent, paw-some, tail wagging, look no further, in today\'s world, when it comes to, we\'ve all been there, without breaking the bank
- FACTS: Only state specs you are certain of from the product data above. Hedge with "many owners report..." or "tends to..." when uncertain. Never invent dimensions, ratings, percentages, or statistics.
- OPENING: Open with a specific relatable moment a dog owner would instantly recognize. Show, don\'t tell. Be specific -- name a real scenario, not a generic one. Bad: "We\'ve all been there..." Good: "I spent $40 on a toy my dog sniffed once and walked away from."
- Voice: first person plural ("we tested", "we found", "we noticed"). NEVER first person singular ("my dog", "I tried").

FORMAT: Return ONLY clean Markdown. No YAML. No preamble. Start writing immediately.
FIRST LINE must be: PIN_DESC: [one punchy sentence, max 20 words, Pinterest stop-scroll hook]
Then article body immediately after."""


def make_review_prompt(title: str, keyword: str, content: str) -> str:
    content_sample = content[:15000] if len(content) > 15000 else content
    return f"""You are a senior human editor for Happy Pet Product Reviews, a budget-focused pet product affiliate blog.
Your job is to score this article honestly and flag specific problems. Do not be generous -- a 3 means acceptable, not good.

IMPORTANT: This article was written by an AI. Your job is to catch what AI typically gets wrong.
Be especially skeptical of:
- Smooth, polished prose that sounds fluent but says nothing specific
- Generic openings that could apply to any article on this topic
- Transitions that feel templated ("In summary...", "Overall...", "Whether you...")
- Warmth that feels performed rather than genuine
- Lists of features that read like spec sheets dressed as prose
- Claims about product performance not grounded in a specific, verifiable detail
- First-person singular voice ("my dog", "my cat", "I tried") -- the blog voice is first-person plural
- Em dashes (--) anywhere -- flag every instance
Score 4 or 5 only if you would genuinely not suspect AI wrote it. When in doubt, score lower.

ARTICLE TITLE: {title}
FOCUS KEYWORD: {keyword}

ARTICLE CONTENT:
---
{content_sample}
---

SCORING RUBRIC:
human_voice: 5=real person genuine opinion | 4=mostly natural minor stiffness | 3=competent but generic | 2=AI-patterned no personality | 1=pure marketing copy
warmth: 5=advice from a knowledgeable friend | 4=friendly but slightly distant | 3=neutral informative | 2=clinical detached | 1=cold robotic
readability: 5=flows effortlessly | 4=good flow 1-2 awkward spots | 3=readable some re-reading | 2=frequent long sentences | 1=difficult to follow
accuracy: 5=all claims verifiable or hedged | 4=mostly accurate one minor issue | 3=soft assertions plausible | 2=multiple unverified specs | 1=significant errors

PASS criteria (ALL must be true):
- human_voice >= 4
- warmth >= 4
- readability >= 3
- accuracy >= 3
- affiliate_link_present = true (amzn.to link present)
- Roundup alternatives must have specific concrete descriptions -- not vague filler

Return ONLY a single valid JSON object. No preamble, no markdown fences, no trailing text.
Begin with {{ and end with }}.

JSON format (exact):
{{"pass": true, "scores": {{"human_voice": 4, "warmth": 4, "readability": 4, "accuracy": 4}}, "affiliate_link_present": true, "em_dash_count": 0, "ai_cliches_found": [], "flags": [], "rewrite_instructions": ""}}

Rules:
- rewrite_instructions: name exact sections and specific fixes if pass=false; empty string if pass=true
- flags: list each specific problem as a plain string; empty array if none
- em_dash_count: exact integer count of em dash characters found
- ai_cliches_found: list detected cliches (logging only -- does not cause fail unless human_voice < 4)"""


def run_generator(product: dict) -> tuple:
    """Returns (content, model_used, tokens, elapsed)"""
    prompt = make_prompt(product)
    log(f'Generating: {product["title"][:60]}')
    t0 = time.time()

    # Primary: OR gpt-oss-120b:free
    try:
        payload = json.dumps({
            'model': OR_GEN_MODEL,
            'messages': [{'role': 'user', 'content': prompt}],
            'max_tokens': 8192,
            'temperature': 0.75,
        }).encode()
        headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {OR_KEY}', **OR_HEADERS}
        raw     = http_post(OPENROUTER_URL, payload, headers, label='OR-Gen', timeout=90, retries=2, backoff_base=60)
        data    = json.loads(raw)
        content = data['choices'][0]['message']['content']
        tokens  = data.get('usage', {}).get('completion_tokens', '?')
        finish  = data['choices'][0].get('finish_reason', '?')
        elapsed = round(time.time() - t0, 1)
        log(f'  Generator OK: {OR_GEN_MODEL} | {len(content)} chars | {tokens} tokens | finish={finish} | {elapsed}s')
        return content, OR_GEN_MODEL, tokens, elapsed
    except Exception as e:
        log(f'  OR primary failed: {e} -- trying Gemini fallback', 'WARN')

    # Fallback: Gemini gemini-2.5-flash-lite
    try:
        gem_url = f'https://generativelanguage.googleapis.com/v1beta/models/{OR_GEN_FALLBACK}:generateContent?key={GEMINI_KEY}'
        payload = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': 8192, 'temperature': 0.75}
        }).encode()
        req = urllib.request.Request(gem_url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        content = data['candidates'][0]['content']['parts'][0]['text']
        elapsed = round(time.time() - t0, 1)
        log(f'  Generator OK: {OR_GEN_FALLBACK} (fallback) | {len(content)} chars | {elapsed}s')
        return content, OR_GEN_FALLBACK, '?', elapsed
    except Exception as e:
        raise RuntimeError(f'Generator exhausted: {e}')


def run_reviewer(title: str, keyword: str, content: str) -> tuple:
    """Returns (scorecard dict, model_used, elapsed)"""
    prompt = make_review_prompt(title, keyword, content)
    log(f'Reviewing...')
    t0 = time.time()
    raw = None

    # Primary: Gemini gemini-2.5-flash-lite direct
    try:
        gem_url = f'https://generativelanguage.googleapis.com/v1beta/models/{REVIEWER_MODEL}:generateContent?key={GEMINI_KEY}'
        payload = json.dumps({
            'contents': [{'parts': [{'text': prompt}]}],
            'generationConfig': {'maxOutputTokens': 400, 'temperature': 0.1}
        }).encode()
        req = urllib.request.Request(gem_url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
        raw = data['candidates'][0]['content']['parts'][0]['text'].strip()
        if not raw:
            raise ValueError('empty content from Gemini reviewer')
        model_used = REVIEWER_MODEL + ' (Gemini direct)'
    except Exception as e:
        log(f'  Gemini reviewer failed: {e} -- trying OR fallback', 'WARN')
        raw = None

    # Fallback: OR gpt-oss-20b:free
    if raw is None:
        try:
            payload = json.dumps({
                'model': REVIEWER_FALLBACK,
                'messages': [{'role': 'user', 'content': prompt}],
                'max_tokens': 400,
                'temperature': 0.1,
            }).encode()
            headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {OR_KEY}', **OR_HEADERS}
            resp    = http_post(OPENROUTER_URL, payload, headers, label='Reviewer-OR', timeout=60, retries=1, backoff_base=30)
            raw     = json.loads(resp)['choices'][0]['message']['content'].strip()
            if not raw:
                raise ValueError('empty content from OR reviewer')
            model_used = REVIEWER_FALLBACK + ' (OR fallback)'
        except Exception as e:
            raise RuntimeError(f'Reviewer exhausted: {e}')

    elapsed = round(time.time() - t0, 1)

    # Parse JSON
    clean = re.sub(r'```json|```|<think>.*?</think>', '', raw, flags=re.DOTALL).strip()
    m = re.search(r'\{.*\}', clean, re.DOTALL)
    scorecard = json.loads(m.group(0)) if m else json.loads(clean)
    log(f'  Reviewer OK: {model_used} | {elapsed}s')
    return scorecard, model_used, elapsed


def print_report(product, content, gen_model, gen_tokens, gen_time, scorecard, rev_model, rev_time):
    scores = scorecard.get('scores', {})
    passed = scorecard.get('pass', False)
    flags  = scorecard.get('flags', [])
    cliches= scorecard.get('ai_cliches_found', [])
    em     = scorecard.get('em_dash_count', 0)
    rewrite= scorecard.get('rewrite_instructions', '')
    affiliate = scorecard.get('affiliate_link_present', False)
    wc = len(content.split())

    # Check first-person in content ourselves as ground truth
    fp_matches = re.findall(r'\bmy (dog|cat|pet)\b|\bI (tried|found|noticed|tested|picked|spent)\b', content, re.I)

    print('\n' + '='*65)
    print('PIPELINE TEST REPORT')
    print('='*65)
    print(f'Topic:     {product["title"]}')
    print(f'Generator: {gen_model} | {wc} words | {gen_tokens} tokens | {gen_time}s')
    print(f'Reviewer:  {rev_model} | {rev_time}s')
    print()
    print(f'RESULT:    {"PASS" if passed else "FAIL"}')
    print(f'Scores:    human_voice={scores.get("human_voice","?")} warmth={scores.get("warmth","?")} '
          f'readability={scores.get("readability","?")} accuracy={scores.get("accuracy","?")}')
    print(f'Affiliate: {"YES" if affiliate else "NO -- MISSING"}')
    print(f'Em dashes: {em}')
    print(f'Word count: {wc} (target: 950-1100)')
    print(f'First-person singular: {len(fp_matches)} instance(s) {fp_matches if fp_matches else ""}')
    if cliches:
        print(f'Cliches:   {", ".join(cliches)}')
    if flags:
        print(f'\nFLAGS:')
        for f in flags:
            print(f'  - {f}')
    if rewrite:
        print(f'\nREWRITE INSTRUCTIONS:')
        print(f'  {rewrite}')
    print()
    print('ARTICLE PREVIEW (first 500 chars):')
    print('-'*40)
    # Strip PIN_DESC line
    body = content
    if body.startswith('PIN_DESC:'):
        _, _, body = body.partition('\n')
    print(body[:500])
    print('-'*40)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--topic', default='', help='Topic slug to test (default: built-in test product)')
    parser.add_argument('--save', action='store_true', help='Save article to /tmp/pipeline_test_output.md')
    args = parser.parse_args()

    if not OR_KEY:
        print('ERROR: OPENROUTER_API_KEY not set'); sys.exit(1)
    if not GEMINI_KEY:
        print('ERROR: GEMINI_API_KEY not set'); sys.exit(1)

    # Load product from products.json if topic specified
    product = TEST_PRODUCT
    if args.topic:
        try:
            products = json.loads((Path(__file__).parent / 'products.json').read_text())
            match = next((p for p in products if p.get('topic') == args.topic), None)
            if match:
                product = match
                log(f'Loaded product from products.json: {args.topic}')
            else:
                log(f'Topic "{args.topic}" not found in products.json -- using default', 'WARN')
        except Exception as e:
            log(f'Could not load products.json: {e} -- using default', 'WARN')

    log(f'Starting pipeline test -- generator={OR_GEN_MODEL}, reviewer={REVIEWER_MODEL}')
    log(f'OR_KEY: {OR_KEY[:8]}... | GEMINI_KEY: {GEMINI_KEY[:8]}...')

    # Step 1: Generate
    content, gen_model, gen_tokens, gen_time = run_generator(product)

    # Step 2: Review
    scorecard, rev_model, rev_time = run_reviewer(product['title'], product['keyword'], content)

    # Step 3: Report
    print_report(product, content, gen_model, gen_tokens, gen_time, scorecard, rev_model, rev_time)

    if args.save:
        out = Path('/tmp/pipeline_test_output.md')
        out.write_text(content)
        log(f'Article saved to {out}')

    passed = scorecard.get('pass', False)
    sys.exit(0 if passed else 1)


if __name__ == '__main__':
    main()
