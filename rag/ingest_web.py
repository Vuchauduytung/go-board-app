"""Index the web pages of rag.web_sources into the books collection, as page_kind "web" with their link.

    python -m rag.ingest_web            # every page
    python -m rag.ingest_web --page ID  # one page
"""
import argparse
import json
import re
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser

from rag.core import COLLECTION, embed
from rag.ingest_books import MIN_CHARS, ensure_collection, split
from rag.web_sources import BY_ID, PAGES, TOPIC

UA = {'User-Agent': 'Mozilla/5.0 (go-scan; Go study app)'}
SKIP = {'script', 'style', 'nav', 'header', 'footer', 'aside', 'noscript', 'svg', 'form', 'button', 'iframe'}
BLOCK = {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr', 'section', 'article', 'blockquote', 'pre'}


class _Text(HTMLParser):
    """The readable text of a page: the <article> or <main> when there is one, without menus and scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip, self.inside, self.found = {'all': [], 'main': []}, 0, 0, False

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag in ('article', 'main'):
            self.inside += 1
            self.found = True
        if tag in BLOCK:
            self._add('\n')

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag in ('article', 'main') and self.inside:
            self.inside -= 1
        if tag in BLOCK:
            self._add('\n')

    def handle_data(self, data):
        if not self.skip:
            self._add(data)

    def _add(self, s):
        self.parts['all'].append(s)
        if self.inside:
            self.parts['main'].append(s)

    def text(self):
        t = ''.join(self.parts['main'] if self.found and len(''.join(self.parts['main'])) > 500 else self.parts['all'])
        t = re.sub(r'[ \t\xa0]+', ' ', t)
        return re.sub(r'\n\s*\n+', '\n\n', t).strip()


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return r.read().decode(r.headers.get_content_charset() or 'utf-8', errors='replace')


def page_text(page):
    if page['kind'] == 'wikipedia':
        title = urllib.parse.unquote(page['url'].rsplit('/', 1)[1])
        api = ('https://en.wikipedia.org/w/api.php?action=query&prop=extracts&explaintext=1&format=json&titles='
               + urllib.parse.quote(title))
        pages = json.loads(_get(api))['query']['pages']
        return next(iter(pages.values())).get('extract', '')
    if page['kind'] == 'discourse':
        topic = page['url'].rstrip('/').rsplit('/', 1)[1]
        raw = _get(f'https://forums.online-go.com/raw/{topic}')
        return re.sub(r'!\[[^\]]*\]\([^)]*\)', '', raw)   # images are lost anyway
    p = _Text()
    p.feed(_get(page['url']))
    return p.text()


def ingest(page):
    from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct
    text = page_text(page)
    chunks = [dict(book_id=f'web-{page["id"]}', title=page['title'], topic=TOPIC, lang=page['lang'], page=0,
                   page_kind='web', chapter='', url=page['url'], part=k, ocr=False, text=piece)
              for k, piece in enumerate(split(text)) if len(piece) >= MIN_CHARS]
    if not chunks:
        return 0
    vectors = embed([f'{c["title"]}\n{c["text"]}' for c in chunks])
    c = ensure_collection()
    c.delete(COLLECTION, points_selector=Filter(must=[FieldCondition(key='book_id', match=MatchValue(value=chunks[0]['book_id']))]),
             wait=True)
    points = [PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f'{ch["book_id"]}:{ch["part"]}')), vector=v, payload=ch)
              for ch, v in zip(chunks, vectors)]
    c.upsert(COLLECTION, points=points, wait=True)
    return len(points)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', choices=sorted(BY_ID))
    args = ap.parse_args()
    for page in [BY_ID[args.page]] if args.page else PAGES:
        try:
            n = ingest(page)
            print(f'{page["id"]:28s} chunks={n:4d}', flush=True)
        except Exception as ex:
            print(f'{page["id"]:28s} FAILED {type(ex).__name__}: {ex}', flush=True)


if __name__ == '__main__':
    main()
