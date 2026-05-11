import sys, json, urllib.request, urllib.error, time, os, re
sys.path.insert(0, '/home/derek/vault/utils/core-skills')

try:
    from brain import get_secret
    or_key     = os.environ.get('OPENROUTER_API_KEY') or get_secret('OPENROUTER_API_KEY', 'global')
    gemini_key = os.environ.get('GEMINI_API_KEY') or get_secret('GEMINI_API_KEY', 'global') or get_secret('GOOGLE_API_KEY', 'global')
    groq_key   = os.environ.get('GROQ_API_KEY') or get_secret('GROQ_API_KEY', 'global')
except Exception:
    or_key     = os.environ.get('OPENROUTER_API_KEY','')
    gemini_key = os.environ.get('GEMINI_API_KEY','')
    groq_key   = os.environ.get('GROQ_API_KEY','')

or_url   = 'https://openrouter.ai/api/v1/chat/completions'
groq_url = 'https://api.groq.com/openai/v1/chat/completions'
or_hdrs  = {'Content-Type':'application/json','Authorization':f'Bearer {or_key}','HTTP-Referer':'https://happypetproductreviews.com','X-Title':'HappyPetAudit'}
groq_hdrs= {'Content-Type':'application/json','Authorization':f'Bearer {groq_key}'}

GEN_PROMPT = """You are a writer for Happy Pet Product Reviews. Write a 200-word intro paragraph for an article titled "Best Dog Puzzle Toys for Smart Dogs".
Requirements: sound like a real dog owner with genuine experience; include one specific concrete detail; avoid cliches (game-changer, look no further, delve, comprehensive, in today's world); no em dashes; warm but not gushing.
Write only the paragraph, no title or preamble."""

REVIEW_PROMPT = """Score this paragraph. Return ONLY valid JSON, no markdown, no preamble, no explanation.
PARAGRAPH: "My dog Biscuit figured out her first puzzle toy in about 45 seconds flat, which is both impressive and slightly embarrassing given I paid $18 for it. That said, watching her work through harder ones has become my favorite 20 minutes of the day. Dog puzzle toys genuinely tire out smart breeds in ways that a regular walk sometimes does not. For Border Collies, Australian Shepherds, and similarly wired dogs, a good puzzle toy is a sanity tool. This guide covers the ones that actually hold up, ranked by durability, difficulty progression, and whether your dog will lose interest after day three."
Return exactly: {"human_voice":<1-5>,"warmth":<1-5>,"readability":<1-5>,"notes":"<10 words max>"}"""

def call(url, model, prompt, hdrs, max_tokens=400, temp=0.7, sys_msg=None, is_gemini=False):
    t0 = time.time()
    try:
        if is_gemini:
            gem_url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}'
            body = json.dumps({'contents':[{'parts':[{'text':prompt}]}],'generationConfig':{'maxOutputTokens':max_tokens,'temperature':temp}}).encode()
            req = urllib.request.Request(gem_url, data=body, headers={'Content-Type':'application/json'}, method='POST')
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
                text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                finish = data['candidates'][0].get('finishReason','?')
                return text, finish, round(time.time()-t0,1), '?'
        else:
            msgs = ([{'role':'system','content':sys_msg}] if sys_msg else []) + [{'role':'user','content':prompt}]
            body = json.dumps({'model':model,'messages':msgs,'max_tokens':max_tokens,'temperature':temp}).encode()
            req = urllib.request.Request(url, data=body, headers=hdrs, method='POST')
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read())
                text = (data['choices'][0]['message'].get('content') or '').strip()
                finish = data['choices'][0].get('finish_reason','?')
                toks = data.get('usage',{}).get('completion_tokens','?')
                return text, finish, round(time.time()-t0,1), toks
    except urllib.error.HTTPError as e:
        return None, f'HTTP {e.code}: {e.read().decode()[:80]}', round(time.time()-t0,1), 0
    except Exception as e:
        return None, str(e)[:80], round(time.time()-t0,1), 0

tests = [
    ('OR',    or_url,   'openai/gpt-oss-120b:free',                                      None,        False),
    ('OR',    or_url,   'openai/gpt-oss-20b:free',                                        None,        False),
    ('OR',    or_url,   'nvidia/nemotron-3-super-120b-a12b:free',                         None,        False),
    ('OR',    or_url,   'nvidia/nemotron-nano-9b-v2:free',                                None,        False),
    ('OR',    or_url,   'qwen/qwen3-32b',                                                 '/no_think', False),
    ('OR',    or_url,   'qwen/qwen3-next-80b-a3b-instruct:free',                         '/no_think', False),
    ('OR',    or_url,   'nousresearch/hermes-3-llama-3.1-405b:free',                      None,        False),
    ('OR',    or_url,   'meta-llama/llama-3.3-70b-instruct:free',                         None,        False),
    ('OR',    or_url,   'arcee-ai/trinity-large-thinking:free',                           None,        False),
    ('OR',    or_url,   'poolside/laguna-m.1:free',                                       None,        False),
    ('OR',    or_url,   'google/gemma-3-27b-it',                                          None,        False),
    ('OR',    or_url,   'cognitivecomputations/dolphin-mistral-24b-venice-edition:free',  None,        False),
    ('Groq',  groq_url, 'llama-3.3-70b-versatile',                                       None,        False),
    ('Groq',  groq_url, 'llama-4-maverick-17b-128e-instruct',                             None,        False),
    ('Groq',  groq_url, 'llama-4-scout-17b-16e-instruct',                                 None,        False),
    ('Groq',  groq_url, 'llama-3.1-8b-instant',                                          None,        False),
    ('Groq',  groq_url, 'gemma2-9b-it',                                                   None,        False),
    ('Groq',  groq_url, 'deepseek-r1-distill-llama-70b',                                 None,        False),
    ('Groq',  groq_url, 'qwen-qwq-32b',                                                   None,        False),
    ('Groq',  groq_url, 'compound-beta',                                                  None,        False),
    ('Gemini',None,     'gemini-2.5-flash',                                               None,        True),
    ('Gemini',None,     'gemini-2.5-flash-lite',                                          None,        True),
]

results = []
print('=== GENERATION + REVIEW QUALITY AUDIT ===\n')

for provider, url, model, sys_msg, is_gem in tests:
    hdrs = groq_hdrs if provider == 'Groq' else or_hdrs

    content, finish, elapsed, tokens = call(url, model, GEN_PROMPT, hdrs, 400, 0.7, sys_msg, is_gem)
    gen_ok = bool(content and len(content) > 60 and finish != 'length')
    wc = len(content.split()) if content else 0
    em = '—' in (content or '')

    rev_sys = '/no_think' if 'qwen3' in model else None
    rev_content, rev_finish, rev_elapsed, _ = call(url, model, REVIEW_PROMPT, hdrs, 150, 0.1, rev_sys, is_gem)
    try:
        raw = re.sub(r'```json|```|<think>.*?</think>', '', rev_content or '', flags=re.DOTALL).strip()
        m2 = re.search(r'\{.*\}', raw, re.DOTALL)
        scorecard = json.loads(m2.group(0)) if m2 else {}
        rev_ok = all(k in scorecard for k in ('human_voice','warmth','readability'))
        hv = scorecard.get('human_voice','?')
        wm = scorecard.get('warmth','?')
        rd = scorecard.get('readability','?')
        notes = scorecard.get('notes','')
    except Exception:
        rev_ok = False
        hv = wm = rd = '?'
        notes = (rev_content or str(rev_finish))[:50]

    gen_label = 'PASS' if gen_ok else f'FAIL({finish[:25]})'
    rev_label = 'PASS' if rev_ok else 'FAIL'

    results.append({'provider':provider,'model':model,
        'gen_ok':gen_ok,'gen_label':gen_label,'words':wc,'em_dash':em,'gen_time':elapsed,'tokens':tokens,
        'rev_ok':rev_ok,'rev_label':rev_label,'hv':hv,'wm':wm,'rd':rd,'notes':notes,'rev_time':rev_elapsed,
        'content':content})

    print(f'[{provider}] {model}')
    print(f'  GEN:    {gen_label} | {wc}w | em={em} | {elapsed}s | {tokens}tok')
    print(f'  REVIEW: {rev_label} | hv={hv} wm={wm} rd={rd} | "{notes}" | {rev_elapsed}s')
    if content and gen_ok:
        print(f'  TEXT:   {content[:160]}')
    print()
    time.sleep(4)

with open('/tmp/audit_results.json','w') as f:
    json.dump(results, f, indent=2)

gen_pass  = sum(1 for r in results if r['gen_ok'])
rev_pass  = sum(1 for r in results if r['rev_ok'])
both_pass = sum(1 for r in results if r['gen_ok'] and r['rev_ok'])
print(f'SUMMARY: {gen_pass}/{len(results)} gen | {rev_pass}/{len(results)} review | {both_pass}/{len(results)} both pass')
