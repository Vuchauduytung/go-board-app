# Retrieval over the Go books collection (adapted from cloud-bot/app/embeddings.py + rag.py):
# multilingual-E5 embeddings on CPU, Qdrant cosine search.

import os
from dataclasses import dataclass
from functools import lru_cache

EMBEDDING_MODEL = os.environ.get('BOOKS_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small')
EMBEDDING_DIMENSIONS = 384
COLLECTION = os.environ.get('BOOKS_COLLECTION', 'go_books_e5')
TOP_K = int(os.environ.get('BOOKS_TOP_K', '6'))
MIN_SCORE = float(os.environ.get('BOOKS_MIN_SCORE', '0.78'))
CONTEXT_MAX_CHARS = int(os.environ.get('BOOKS_CONTEXT_MAX_CHARS', '9000'))


@dataclass(frozen=True)
class Chunk:
    book_id: str
    title: str
    page: int          # PDF page (1-based) or EPUB chapter number
    page_kind: str     # 'page' | 'chapter'
    chapter: str
    text: str
    score: float


@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL, device='cpu')


def embed(texts, query=False):
    prefix = 'query: ' if query else 'passage: '
    return _model().encode([prefix + t for t in texts], normalize_embeddings=True,
                           batch_size=32).tolist()


@lru_cache(maxsize=1)
def qdrant():
    from qdrant_client import QdrantClient
    return QdrantClient(url=os.environ['QDRANT_URL'], api_key=os.environ.get('QDRANT_API_KEY') or None, timeout=20)


def search(queries, top_k=TOP_K, min_score=MIN_SCORE):
    """Search each query (e.g. the user's Vietnamese question and its English translation) separately
    and merge by reciprocal rank: E5 scores same-language passages higher, so merging by raw score
    would let the Vietnamese books crowd out the English ones."""
    if isinstance(queries, str):
        queries = [queries]
    fused, chunks = {}, {}
    for vec in embed(queries, query=True):
        res = qdrant().query_points(collection_name=COLLECTION, query=vec, limit=top_k,
                                    score_threshold=min_score, with_payload=True)
        for rank, p in enumerate(res.points):
            fused[p.id] = fused.get(p.id, 0.0) + 1.0 / (60 + rank)
            if p.id not in chunks or chunks[p.id].score < p.score:
                chunks[p.id] = Chunk(book_id=p.payload['book_id'], title=p.payload['title'], page=int(p.payload['page']),
                                     page_kind=p.payload['page_kind'], chapter=p.payload.get('chapter', ''),
                                     text=p.payload['text'], score=float(p.score))
    return [chunks[i] for i in sorted(fused, key=fused.get, reverse=True)[:top_k]]


def format_context(chunks):
    blocks, used = [], 0
    for i, c in enumerate(chunks, 1):
        where = f'trang {c.page}' if c.page_kind == 'page' else f'chương "{c.chapter}"'
        block = f'[{i}] {c.title} — {where}\n{c.text}'
        block = block[:max(0, CONTEXT_MAX_CHARS - used)]
        if not block:
            break
        blocks.append(block)
        used += len(block) + 2
    return '\n\n'.join(blocks)
