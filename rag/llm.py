# Text generation over a chain of free LLM providers: the first one that answers wins; an overloaded model, a
# used-up free quota or any other error moves on to the next. Providers without an API key are skipped.
# Gemini goes through google-genai; Groq, OpenRouter, Mistral and GitHub Models share the OpenAI chat API.
#
#   LLM_CHAIN_BOARD / LLM_CHAIN_BOOKS / LLM_CHAIN_TRANSLATE = "provider:model,provider:model,..."

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger('llm')

OPENAI_COMPATIBLE = {   # provider -> (chat completions URL, API key variable)
    'groq': ('https://api.groq.com/openai/v1/chat/completions', 'GROQ_API_KEY'),
    'openrouter': ('https://openrouter.ai/api/v1/chat/completions', 'OPENROUTER_API_KEY'),
    'mistral': ('https://api.mistral.ai/v1/chat/completions', 'MISTRAL_API_KEY'),
    'github': ('https://models.github.ai/inference/chat/completions', 'GITHUB_MODELS_TOKEN'),
}
KEYS = {'gemini': 'GEMINI_API_KEY', **{p: v[1] for p, v in OPENAI_COMPATIBLE.items()}}

CHAINS = {
    # explaining positions: the strongest free models first
    'board': 'gemini:gemini-3.5-flash,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b,'
             'mistral:mistral-medium-latest,github:openai/gpt-4.1-mini,openrouter:deepseek/deepseek-chat-v3.1:free',
    'books': 'gemini:gemini-3.5-flash-lite,gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,'
             'mistral:mistral-small-latest,github:openai/gpt-4.1-mini',
    'translate': 'gemini:gemini-3.5-flash-lite,groq:llama-3.1-8b-instant,mistral:mistral-small-latest',
}
# Gemini 3 models think before answering, out of the same output budget: a little for positions, none otherwise
THINKING = {'board': 'low', 'books': 'minimal', 'translate': 'minimal'}
TIMEOUT = 60


class Unavailable(Exception):
    """Every provider of the chain failed."""


@dataclass
class Reply:
    text: str
    tokens: int
    model: str   # "provider:model" that answered


def chain(name):
    """[(provider, model)] of a chain, keeping only providers that have an API key."""
    spec = os.environ.get(f'LLM_CHAIN_{name.upper()}', CHAINS[name])
    out = []
    for item in spec.split(','):
        provider, _, model = item.strip().partition(':')
        if provider in KEYS and model and os.environ.get(KEYS[provider]):
            out.append((provider, model))
    return out


@lru_cache(maxsize=1)
def _gemini():
    from google import genai
    return genai.Client(api_key=os.environ['GEMINI_API_KEY'])


def _call_gemini(model, system, messages, max_tokens, schema, temperature, thinking=None):
    from google.genai import types
    config = {'system_instruction': system, 'max_output_tokens': max_tokens}
    if thinking:
        config['thinking_config'] = types.ThinkingConfig(thinking_level=thinking)
        if thinking != 'minimal':
            config['max_output_tokens'] += 1024
    if temperature is not None:
        config['temperature'] = temperature
    if schema:
        config |= {'response_mime_type': 'application/json', 'response_schema': schema}
    resp = _gemini().models.generate_content(
        model=model,
        contents=[types.Content(role='model' if m['role'] == 'assistant' else 'user', parts=[types.Part(text=m['content'])])
                  for m in messages],
        config=types.GenerateContentConfig(**config))
    meta = getattr(resp, 'usage_metadata', None)
    return (resp.text or '').strip(), int(getattr(meta, 'total_token_count', 0) or 0)


def _call_openai(provider, model, system, messages, max_tokens, schema, temperature):
    url, key = OPENAI_COMPATIBLE[provider]
    if schema:   # JSON mode; the shape itself is described in the system prompt
        system += '\n\nChỉ trả lời bằng đúng một đối tượng JSON như mô tả ở trên, không thêm chữ nào khác.'
    body = {'model': model, 'messages': [{'role': 'system', 'content': system}] + messages,
            'max_tokens': max_tokens * (2 if 'gpt-oss' in model else 1)}   # gpt-oss also spends tokens reasoning
    if 'gpt-oss' in model:
        body['reasoning_effort'] = 'low'
    if schema:
        body['response_format'] = {'type': 'json_object'}
    if temperature is not None:
        body['temperature'] = temperature
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ[KEYS[provider]]}
    if provider == 'openrouter':
        headers['X-Title'] = 'Go Scan'
    req = urllib.request.Request(url, json.dumps(body).encode(), headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f'HTTP {ex.code}: {ex.read()[:300].decode(errors="replace")}') from None
    text = (data['choices'][0]['message'].get('content') or '').strip()
    return text, int((data.get('usage') or {}).get('total_tokens') or 0)


def generate(chain_name, system, messages, max_tokens, schema=None, temperature=None):
    """messages: [{"role": "user" | "assistant", "content": ...}]. schema: Gemini response schema for JSON
    answers (other providers get JSON mode). -> Reply; raises Unavailable when every provider failed."""
    errors = []
    for provider, model in chain(chain_name):
        try:
            if provider == 'gemini':
                text, tokens = _call_gemini(model, system, messages, max_tokens, schema, temperature,
                                            THINKING.get(chain_name))
            else:
                text, tokens = _call_openai(provider, model, system, messages, max_tokens, schema, temperature)
        except Exception as ex:
            log.warning('%s:%s failed, trying the next model: %s', provider, model, str(ex)[:300])
            errors.append(f'{provider}:{model}: {str(ex)[:120]}')
            continue
        if text:
            return Reply(text, tokens, f'{provider}:{model}')
        errors.append(f'{provider}:{model}: empty answer')
    raise Unavailable('Không có mô hình AI nào trả lời được lúc này (' + ('; '.join(errors) or 'chưa có API key') + ')')
