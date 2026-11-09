"""
guidelines_server.py — MCP Server #3: Guidelines Knowledge Base

Exposes:
  Tool:     search_guidelines (backed by FAISS vector search when available,
            keyword matching when RAG deps not installed)
  Resource: guidelines://all  (all guidelines as combined text)

MILESTONE 3 — REAL RAG:
  The tool interface is IDENTICAL to the M2 stub. Only the internals changed.
  This is the power of the MCP abstraction: the agent code didn't change at all.

HOW RAG WORKS:
  1. At server startup, vectorstore/rag.py chunks all 5 guideline .md files
     into ~40 paragraph-sized pieces.
  2. Each chunk is embedded using sentence-transformers/all-MiniLM-L6-v2
     (384-dim dense vector, no API key needed).
  3. Vectors are stored in a FAISS IndexFlatIP (exact cosine similarity search).
  4. At query time the query is embedded and the top-k nearest chunks are returned.

INTERVIEW TALKING POINT:
  "The server interface is a contract. Because the agent calls tools/list to
  discover the tool at runtime, I was able to swap keyword search for real
  vector search without changing a single line of agent or client code."
"""

import os
import sys
from pathlib import Path

from fastmcp import FastMCP

mcp = FastMCP(name="guidelines-kb")

GUIDELINES_DIR = Path(__file__).parent.parent / "data" / "guidelines"
# Add project root to path so vectorstore/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

# Try to load the RAG backend; fall back to keyword search gracefully
_rag = None
_rag_error = None
try:
    from vectorstore.rag import get_rag
    _rag = get_rag()   # Builds or loads index on first import
    _search_mode = "vector"
except Exception as e:
    _rag_error = str(e)
    _search_mode = "keyword"


def _load_all_guidelines() -> dict[str, str]:
    guidelines = {}
    for f in sorted(GUIDELINES_DIR.glob("*.md")):
        guidelines[f.stem] = f.read_text(encoding="utf-8")
    return guidelines


def _keyword_search(query: str, top_k: int) -> list[dict]:
    """Fallback: simple keyword scoring."""
    guidelines = _load_all_guidelines()
    query_words = set(query.lower().split())
    scored = []
    for name, content in guidelines.items():
        score = sum(1 for w in query_words if w in content.lower())
        scored.append((score, name, content))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"guideline_id": name, "relevance_score": score, "excerpt": content[:600]}
        for score, name, content in scored[:top_k]
    ]


# ---------------------------------------------------------------------------
# TOOL: search_guidelines (Milestone 3: real FAISS vector search)
# ---------------------------------------------------------------------------
@mcp.tool()
def search_guidelines(query: str, top_k: int = 3) -> dict:
    """
    Search the insurance guidelines knowledge base for relevant guidelines.

    Returns the top-k most relevant guideline excerpts with their titles
    and similarity scores.

    Uses FAISS vector similarity search with sentence-transformer embeddings
    when available; falls back to keyword matching if RAG deps are not installed.

    Args:
        query: Natural language query describing the claim situation.
               e.g. "theft claim filed shortly after policy started"
        top_k: Maximum number of guideline excerpts to return (default 3).
    """
    if _rag is not None:
        results = _rag.search(query, top_k=top_k)
    else:
        results = _keyword_search(query, top_k)

    return {
        "query": query,
        "search_mode": _search_mode,
        "rag_error": _rag_error,
        "results": results,
    }


# ---------------------------------------------------------------------------
# RESOURCE: guidelines://all
#
# Returns ALL guidelines concatenated. The agent (or LLM) can pull this
# as background context. Resources are read-only — no arguments allowed.
# ---------------------------------------------------------------------------
@mcp.resource("guidelines://all")
def all_guidelines() -> str:
    """
    Returns all insurance guidelines as a single combined document.
    Useful for populating the LLM context window with full background knowledge.
    """
    guidelines = _load_all_guidelines()
    sections = []
    for name, content in guidelines.items():
        sections.append(f"# {name.upper()}\n\n{content}")
    return "\n\n---\n\n".join(sections)


if __name__ == "__main__":
    mcp.run()
