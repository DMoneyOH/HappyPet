import json, urllib.request, urllib.error, os, sys

groq_key = os.environ.get('GROQ_API_KEY', '')
or_key = os.environ.get('OPENROUTER_API_KEY', '')

groq_url = 'https://api.groq.com/openai/v1/chat/completions'
or_url = 'https://openrouter.ai/api/v1/chat/completions'
prompt = 'Reply with one word: working'

def test(label, url, model, key, extra_headers={}):
    payload = json.dumps({
        'model': model,
        'messages': [{'role': 'user', 'content': prompt}],
        'max_tokens': 10, 'temperature': 0.0,
    }).encode()
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {key}', **extra_headers}
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method='POST')
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
            content = (data['choices'][0]['message'].get('content') or '').strip()
            finish = data['choices'][0].get('finish_reason','?')
            print(f'PASS  [{label}] {model} -- {repr(content)} [{finish}]')
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:150]
        print(f'FAIL  [{label}] {model} -- HTTP {e.code}: {body}')
    except Exception as e:
        print(f'FAIL  [{label}] {model} -- {e}')

or_headers = {'HTTP-Referer': 'https://happypetproductreviews.com', 'X-Title': 'HappyPetAudit'}

print('=== GROQ ===')
for m in [
    'llama-3.3-70b-versatile',
    'llama-3.3-70b-instruct', 
    'llama-3.1-8b-instant',
    'llama-4-scout-17b-16e-instruct',
    'meta-llama/llama-4-scout-17b-16e-instruct',
    'compound-mini',
    'llama-3.1-70b-versatile',
    'gemma2-9b-it',
    'mixtral-8x7b-32768',
]:
    test('Groq', groq_url, m, groq_key)

print()
print('=== OPENROUTER ===')
for m in [
    'openai/gpt-oss-120b:free',
    'qwen/qwen3-32b',
    'nvidia/nemotron-3-super-120b-a12b:free',
    'meta-llama/llama-3.3-70b-instruct:free',
    'nousresearch/hermes-3-llama-3.1-405b:free',
    'google/gemma-4-27b-it:free',
    'qwen/qwen3-30b-a3b:free',
    'mistralai/mistral-7b-instruct:free',
]:
    test('OR', or_url, m, or_key, or_headers)
