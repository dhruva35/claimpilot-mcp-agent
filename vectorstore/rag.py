"""
rag.py — FAISS Vector Store for Guidelines Search (Milestone 3)

HOW RAG WORKS HERE:
  1. CHUNK: Each guideline .md file is split into paragraphs (~200-400 tokens each).
  2. EMBED: Each chunk is converted to a dense vector using sentence-transformers
     (all-MiniLM-L6-v2 — 384-dimensional, ~80MB, no API key needed).
  3. INDEX: Chunks are added to a FAISS IndexFlatIP (inner product = cosine similarity
     when vectors are L2-normalised).
  4. QUERY: At search time the query is embedded, normalised, and we retrieve
     the top-k nearest chunk vectors.

WHY FAISS?
  - Pure numpy/C++ — no server process, no network, works offline.
  - IndexFlatIP is an exact (brute-force) search — perfect for 5-8 docs / ~40 chunks.
  - For larger datasets we'd switch to IndexIVFFlat or HNSW.

WHY all-MiniLM-L6-v2?
  - Very small (80MB), fast inference on CPU.
  - Decent semantic quality for English documents.
  - Well known in the RAG community — easy to explain in an interview.

INTERVIEW TALKING POINT:
  "The key insight of RAG is that we don't put all guidelines in the prompt —
  we only retrieve the 2-3 most relevant ones for each query. This keeps the
  context window small, reduces hallucination risk, and scales to thousands
  of guidelines without prompt-size issues."
"""

import json
from pathlib import Path
from typing import NamedTuple

import numpy as np

GUIDELINES_DIR = Path(__file__).parent.parent / "data" / "guidelines"
INDEX_DIR = Path(__file__).parent.parent / "vectorstore"

# Cache file paths — built once, reused on subsequent server starts
CHUNKS_CACHE = INDEX_DIR / "chunks.json"
VECTORS_CACHE = INDEX_DIR / "vectors.npy"


class Chunk(NamedTuple):
    """One searchable text chunk."""
    text: str
    source: str   # guideline filename stem


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _chunk_guideline(source: str, text: str, max_chars: int = 500) -> list[Chunk]:
    """
    Split a guideline into paragraph-sized chunks.
    We chunk by blank-line-separated paragraphs, merging short ones.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    buffer = ""

    for para in paragraphs:
        if len(buffer) + len(para) < max_chars:
            buffer = (buffer + "\n\n" + para).strip()
        else:
            if buffer:
                chunks.append(Chunk(text=buffer, source=source))
            buffer = para

    if buffer:
        chunks.append(Chunk(text=buffer, source=source))

    return chunks


def build_chunks() -> list[Chunk]:
    """Load all guidelines and chunk them."""
    all_chunks = []
    for f in sorted(GUIDELINES_DIR.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        all_chunks.extend(_chunk_guideline(f.stem, content))
    return all_chunks


# ---------------------------------------------------------------------------
# Embedding + Index
# ---------------------------------------------------------------------------

class GuidanceRAG:
    """
    Wraps FAISS index + sentence-transformer for semantic search over guidelines.
    """

    def __init__(self):
        self._model = None   # Lazy-loaded on first use
        self._chunks: list[Chunk] = []
        self._index = None   # FAISS index

    def _get_model(self):
        """Lazy-load the embedding model (expensive on first call)."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer("all-MiniLM-L6-v2")
        return self._model

    def _embed(self, texts: list[str]) -> np.ndarray:
        """
        Embed a list of strings, return L2-normalised float32 array.
        Normalisation makes inner product = cosine similarity.
        """
        model = self._get_model()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vecs.astype(np.float32)

    def build(self, force: bool = False):
        """
        Build (or load from cache) the FAISS index.

        On first run: embeds all chunks and saves to disk.
        On subsequent runs: loads from cache (fast).
        """
        import faiss

        INDEX_DIR.mkdir(parents=True, exist_ok=True)

        # Use cache if available and not forcing rebuild
        if not force and CHUNKS_CACHE.exists() and VECTORS_CACHE.exists():
            with open(CHUNKS_CACHE, "r") as f:
                chunk_data = json.load(f)
            self._chunks = [Chunk(**d) for d in chunk_data]
            vectors = np.load(str(VECTORS_CACHE))
        else:
            self._chunks = build_chunks()
            texts = [c.text for c in self._chunks]
            vectors = self._embed(texts)

            # Save to cache
            with open(CHUNKS_CACHE, "w") as f:
                json.dump([{"text": c.text, "source": c.source} for c in self._chunks], f)
            np.save(str(VECTORS_CACHE), vectors)

        # Build FAISS index: IndexFlatIP = exact inner product search
        dim = vectors.shape[1]  # 384 for all-MiniLM-L6-v2
        self._index = faiss.IndexFlatIP(dim)
        self._index.add(vectors)

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Find the top-k most relevant chunks for a query.

        Returns:
            List of dicts: {guideline_id, relevance_score, excerpt}
        """
        if self._index is None:
            raise RuntimeError("Index not built. Call build() first.")

        query_vec = self._embed([query])  # shape: (1, 384)
        # scores: cosine similarity (0.0 to 1.0 for normalised vectors)
        scores, indices = self._index.search(query_vec, top_k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0:
                continue  # FAISS returns -1 for empty slots
            chunk = self._chunks[idx]
            results.append({
                "guideline_id": chunk.source,
                "relevance_score": float(round(score, 4)),
                "excerpt": chunk.text[:600],
            })
        return results


# Singleton — shared across the server process lifetime
_rag_instance: GuidanceRAG | None = None


def get_rag() -> GuidanceRAG:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = GuidanceRAG()
        _rag_instance.build()
    return _rag_instance

