# Text to speech for the coach's answers (Vietnamese), played by the browser as MP3: devices often have no Vietnamese
# voice of their own, or their speech synthesis stays silent. Microsoft Edge's online neural voice (edge-tts) first;
# when it fails, Google Translate's voice, which takes at most ~200 characters per request (pieces are joined: MP3
# frames simply follow each other). A few recent clips are kept in memory, for "🔊 Nghe lại".
#
#   TTS_VOICE  edge-tts voice (default vi-VN-HoaiMyNeural; vi-VN-NamMinhNeural is the male one)

import asyncio
import hashlib
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
from collections import OrderedDict

log = logging.getLogger('tts')

VOICE = os.environ.get('TTS_VOICE', 'vi-VN-HoaiMyNeural')
TEXT_MAX = 600       # characters per request; the browser sends an answer sentence by sentence
GOOGLE_PIECE = 190
TIMEOUT = 15
_cache = OrderedDict()
_cache_lock = threading.Lock()
CACHE_ITEMS = 200


class Unavailable(Exception):
    pass


def synthesize(text):
    """MP3 of `text` read in Vietnamese. Raises Unavailable when no voice answered."""
    text = re.sub(r'\s+', ' ', text).strip()[:TEXT_MAX]
    key = hashlib.sha1(f'{VOICE}|{text}'.encode()).hexdigest()
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    errors = []
    for name, fn in (('edge', _edge), ('google', _google)):
        try:
            audio = fn(text)
        except Exception as ex:
            log.warning('%s TTS failed: %s', name, str(ex)[:200])
            errors.append(f'{name}: {type(ex).__name__}')
            continue
        if audio:
            try:
                import usage
                usage.add(f'tts:{name}')
            except Exception:
                pass
            with _cache_lock:
                _cache[key] = audio
                while len(_cache) > CACHE_ITEMS:
                    _cache.popitem(last=False)
            return audio
        errors.append(f'{name}: no audio')
    raise Unavailable('Không tạo được giọng đọc lúc này (' + '; '.join(errors) + ')')


def _edge(text):
    import edge_tts

    async def run():
        out = bytearray()
        async for chunk in edge_tts.Communicate(text, VOICE).stream():
            if chunk['type'] == 'audio':
                out += chunk['data']
        return bytes(out)
    return asyncio.run(asyncio.wait_for(run(), TIMEOUT))


def _google(text):
    out = b''
    for piece in _pieces(text, GOOGLE_PIECE):
        url = ('https://translate.google.com/translate_tts?ie=UTF-8&tl=vi&client=tw-ob&q='
               + urllib.parse.quote(piece))
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out += r.read()
    return out


def _pieces(text, size):
    """Text cut at sentence or word boundaries into pieces of at most `size` characters."""
    out, cur = [], ''
    for word in re.split(r'(?<=[.!?;:,])\s+|\s+', text):
        if cur and len(cur) + 1 + len(word) > size:
            out.append(cur)
            cur = word[:size]
        else:
            cur = f'{cur} {word}'.strip()
    return out + [cur] if cur else out
