# Text generation over a chain of free LLM providers: the first one that answers wins; an overloaded model, a
# used-up free quota or any other error moves on to the next. Providers without an API key are skipped.
# Gemini goes through google-genai; Groq, OpenRouter and Mistral share the OpenAI chat API.
# (GitHub Models was retired on 2026-07-30.)
#
#   LLM_CHAIN_BOARD / LLM_CHAIN_BOOKS / LLM_CHAIN_TRANSLATE / LLM_CHAIN_FEEDBACK = "provider:model,provider:model,..."

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
}
KEYS = {'gemini': 'GEMINI_API_KEY', **{p: v[1] for p, v in OPENAI_COMPATIBLE.items()}}

CHAINS = {
    # explaining positions: the strongest free models first
    'board': 'gemini:gemini-3.5-flash,gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-120b,'
             'openrouter:google/gemma-4-31b-it:free,openrouter:nvidia/nemotron-3-ultra-550b-a55b:free,'
             'mistral:mistral-medium-latest,mistral:ministral-14b-latest',
    'books': 'gemini:gemini-3.5-flash-lite,gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,'
             'openrouter:google/gemma-4-31b-it:free,openrouter:nvidia/nemotron-3-ultra-550b-a55b:free,'
             'mistral:mistral-small-latest,mistral:ministral-14b-latest',
    'translate': 'gemini:gemini-3.5-flash-lite,groq:openai/gpt-oss-20b,mistral:mistral-small-latest,'
                 'mistral:ministral-14b-latest',
    # the admin bot summarising user reviews (telegram_bot.py)
    'feedback': 'gemini:gemini-3.5-flash-lite,gemini:gemini-3.5-flash,groq:openai/gpt-oss-120b,'
                'mistral:mistral-small-latest,mistral:ministral-14b-latest',
}
# Gemini 3 models think before answering, out of the same output budget: a little for positions, none otherwise
THINKING = {'board': 'low', 'books': 'minimal', 'translate': 'minimal', 'feedback': 'minimal'}
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
    # Groq sits behind Cloudflare, which rejects urllib's default User-Agent (error 1010)
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + os.environ[KEYS[provider]],
               'User-Agent': 'go-scan/1.0'}
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


# Speech to text for the voice questions, when the browser cannot transcribe them itself
STT_CHAIN = 'groq:whisper-large-v3-turbo,gemini:gemini-3.5-flash-lite'
STT_PROMPT = 'Hỏi đáp về cờ vây: nước đi, tọa độ như Q16, D4, K10, quân đen, quân trắng, biến, KataGo.'


def transcribe(audio, mime, language='vi'):
    """Text of a short recording (webm / ogg / mp4 / wav). -> str; raises Unavailable when every provider failed."""
    errors = []
    for item in os.environ.get('LLM_CHAIN_STT', STT_CHAIN).split(','):
        provider, _, model = item.strip().partition(':')
        if provider not in KEYS or not model or not os.environ.get(KEYS[provider]):
            continue
        try:
            text = (_transcribe_gemini(model, audio, mime, language) if provider == 'gemini'
                    else _transcribe_openai(provider, model, audio, mime, language))
        except Exception as ex:
            log.warning('%s:%s transcription failed, trying the next model: %s', provider, model, str(ex)[:300])
            errors.append(f'{provider}:{model}: {str(ex)[:120]}')
            continue
        if text:
            return text
        errors.append(f'{provider}:{model}: empty transcript')
    raise Unavailable('Không chuyển được giọng nói thành chữ lúc này (' + ('; '.join(errors) or 'chưa có API key') + ')')


def _transcribe_openai(provider, model, audio, mime, language):
    """OpenAI-style /audio/transcriptions (Groq Whisper), multipart upload."""
    url = OPENAI_COMPATIBLE[provider][0].replace('/chat/completions', '/audio/transcriptions')
    ext = {'audio/webm': 'webm', 'audio/ogg': 'ogg', 'audio/mp4': 'm4a', 'audio/mpeg': 'mp3', 'audio/wav': 'wav'}
    b = os.urandom(12).hex()
    fields = {'model': model, 'language': language, 'response_format': 'json', 'prompt': STT_PROMPT}
    body = b''.join(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items())
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="voice.{ext.get(mime, "webm")}"\r\n'
             f'Content-Type: {mime}\r\n\r\n').encode() + audio + f'\r\n--{b}--\r\n'.encode()
    req = urllib.request.Request(url, body, {'Content-Type': f'multipart/form-data; boundary={b}', 'User-Agent': 'go-scan/1.0',
                                             'Authorization': 'Bearer ' + os.environ[KEYS[provider]]})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return (json.loads(r.read()).get('text') or '').strip()
    except urllib.error.HTTPError as ex:
        raise RuntimeError(f'HTTP {ex.code}: {ex.read()[:300].decode(errors="replace")}') from None


def _transcribe_gemini(model, audio, mime, language):
    from google.genai import types
    resp = _gemini().models.generate_content(
        model=model,
        contents=[types.Part.from_bytes(data=audio, mime_type=mime),
                  'Chép lại nguyên văn câu nói tiếng Việt trong đoạn ghi âm (chủ đề: ' + STT_PROMPT + '). '
                  'Chỉ trả về đúng câu nói, không thêm gì khác; im lặng thì trả về chuỗi rỗng.'],
        config=types.GenerateContentConfig(max_output_tokens=400,
                                           thinking_config=types.ThinkingConfig(thinking_level='minimal')))
    return (resp.text or '').strip()
