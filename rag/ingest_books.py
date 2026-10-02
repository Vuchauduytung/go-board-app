"""Index the Go books into Qdrant and render PDF page images for citations.

    python -m rag.ingest_books                 # all books
    python -m rag.ingest_books --book shape-up # one book
    python -m rag.ingest_books --pages-only    # only (re)render page images

Reads books/src/Go Books/<file>, OCR text from books/ocr/<stem>/p<N>.txt, writes page images to
books/pages/<book id>/p<N>.jpg. Credentials come from the environment (QDRANT_URL, QDRANT_API_KEY).
"""

import argparse
import html
import re
import subprocess
import uuid
import zipfile
from pathlib import Path

from rag.catalog import BOOKS, BY_ID
from rag.core import COLLECTION, EMBEDDING_DIMENSIONS, embed, qdrant

ROOT = Path(__file__).resolve().parent.parent / 'books'
SRC, OCR, PAGES = ROOT / 'src' / 'Go Books', ROOT / 'ocr', ROOT / 'pages'
CHUNK, OVERLAP, MIN_CHARS = 1200, 150, 80


def clean(text):
    lines = [l.strip() for l in text.replace('\r', '').split('\n')]
    lines = [l for l in lines if re.search(r'[^\W\d_]', l)]  # drop page numbers, rules, nav glyphs
    text = '\n'.join(lines)
    text = re.sub(r'-\n(\w)', r'\1', text)            # hyphenated line breaks
    return re.sub(r'[ \t]+', ' ', text).strip()


def split(text, size=CHUNK, overlap=OVERLAP):
    """Split on paragraph/sentence/space boundaries into pieces of at most `size` characters."""
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind('\n', start + size // 2, end), text.rfind('. ', start + size // 2, end),
                      text.rfind(' ', start + size // 2, end))
            if cut > start:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [o for o in out if o]


def pdf_pages(path):
    n = int(re.search(r'Pages:\s+(\d+)', subprocess.run(['pdfinfo', str(path)], capture_output=True,
                                                         text=True).stdout).group(1))
    for p in range(1, n + 1):
        txt = subprocess.run(['pdftotext', '-q', '-f', str(p), '-l', str(p), str(path), '-'],
                             capture_output=True, text=True).stdout
        yield p, clean(txt)


def ocr_pages(path):
    d = OCR / path.stem
    for f in sorted(d.glob('p*.txt'), key=lambda f: int(f.stem[1:])):
        yield int(f.stem[1:]), clean(f.read_text(errors='replace'))


def epub_chapters(path):
    z = zipfile.ZipFile(path)
    opf_name = next(n for n in z.namelist() if n.endswith('.opf'))
    opf = z.read(opf_name).decode('utf8', 'ignore')
    base = opf_name.rsplit('/', 1)[0] + '/' if '/' in opf_name else ''
    items = dict(re.findall(r'<item[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf))
    items.update({i: h for h, i in re.findall(r'<item[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf)})
    spine = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
    for n, idref in enumerate(spine, 1):
        raw = z.read(base + items[idref]).decode('utf8', 'ignore')
        title = re.search(r'<title>(.*?)</title>', raw, re.S)
        title = html.unescape(re.sub(r'\s+', ' ', title.group(1))).strip() if title else f'Chương {n}'
        body = re.sub(r'<(script|style|svg)[^>]*>.*?</\1>', ' ', raw, flags=re.S)  # svg = diagrams
        body = re.sub(r'</(p|h\d|li|div|tr)>|<br\s*/?>', '\n', body)
        yield n, title, clean(html.unescape(re.sub(r'<[^>]+>', ' ', body)))


def units(book):
    """(page or chapter number, chapter title, text) for one book."""
    path = SRC / book['file']
    if book['kind'] == 'epub':
        yield from epub_chapters(path)
    else:
        for p, txt in (ocr_pages(path) if book['kind'] == 'ocr' else pdf_pages(path)):
            yield p, '', txt


def render_pages(book, dpi=110):
    if book['kind'] == 'epub':
        return 0
    out = PAGES / book['id']
    out.mkdir(parents=True, exist_ok=True)
    if not any(out.glob('p*.jpg')):
        subprocess.run(['pdftoppm', '-r', str(dpi), '-jpeg', '-jpegopt', 'quality=70',
                        str(SRC / book['file']), str(out / 'p')], check=True)
        for f in out.glob('p-*.jpg'):           # pdftoppm pads page numbers: p-007.jpg -> p7.jpg
            f.rename(out / f'p{int(f.stem.split("-")[1])}.jpg')
    return len(list(out.glob('p*.jpg')))


def ensure_collection():
    from qdrant_client.models import Distance, PayloadSchemaType, VectorParams
    c = qdrant()
    if not c.collection_exists(COLLECTION):
        c.create_collection(COLLECTION, vectors_config=VectorParams(size=EMBEDDING_DIMENSIONS, distance=Distance.COSINE))
    c.create_payload_index(COLLECTION, field_name='book_id', field_schema=PayloadSchemaType.KEYWORD, wait=True)
    return c


def ingest(book):
    from qdrant_client.models import FieldCondition, Filter, MatchValue, PointStruct
    chunks = []
    for num, chapter, text in units(book):
        if len(text) < MIN_CHARS:
            continue
        for k, piece in enumerate(split(text)):
            chunks.append(dict(book_id=book['id'], title=book['title'], topic=book['topic'], lang=book['lang'],
                               page=num, page_kind='chapter' if book['kind'] == 'epub' else 'page',
                               chapter=chapter, part=k, ocr=book['kind'] == 'ocr', text=piece))
    if not chunks:
        print(f'{book["id"]}: no text')
        return 0
    vectors = embed([f'{c["title"]}\n{c["chapter"]}\n{c["text"]}'.strip() for c in chunks])
    c = ensure_collection()
    c.delete(COLLECTION, points_selector=Filter(must=[FieldCondition(key='book_id', match=MatchValue(value=book['id']))]),
             wait=True)
    points = [PointStruct(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f'{ch["book_id"]}:{ch["page"]}:{ch["part"]}')),
                          vector=v, payload=ch) for ch, v in zip(chunks, vectors)]
    for i in range(0, len(points), 256):
        c.upsert(COLLECTION, points=points[i:i + 256], wait=True)
    return len(points)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--book', choices=sorted(BY_ID))
    ap.add_argument('--pages-only', action='store_true')
    args = ap.parse_args()
    for book in [BY_ID[args.book]] if args.book else BOOKS:
        pages = render_pages(book)
        n = 0 if args.pages_only else ingest(book)
        print(f'{book["id"]:28s} chunks={n:5d} page_images={pages}', flush=True)


if __name__ == '__main__':
    main()
