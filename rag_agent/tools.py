import json
import logging
import os
import threading
import unicodedata
from pathlib import Path

from langchain_core.tools import tool
from qdrant_client.http import models as qmodels

from config import (
    DEFAULT_SEARCH_LIMIT,
    PARENT_STORE_PATH,
    RERANKER_MODEL,
    RETRIEVAL_CANDIDATES,
    SOURCE_NAMES,
    reranker_device,
)
from db.document_processor import child_vector_store
from domain_profile import PROFILE

logger = logging.getLogger(__name__)

# Lazy load CrossEncoder to keep startup times fast
reranker = None
reranker_lock = threading.Lock()

def normalize_text(text: str) -> str:
    """Normalize text for matching (lowercase + Unicode compatibility form)."""
    return unicodedata.normalize('NFKC', text.lower())

@tool
def search_documents(query: str, limit: int = DEFAULT_SEARCH_LIMIT, source: str = None) -> str:
    """Search the indexed document corpus and return full parent contexts.

    Use this to gather information about a concept, rule or topic covered by
    the ingested documents.

    Args:
        query: Search query string
        limit: Maximum number of parent documents to return (default from config)
        source: Optional source file name to filter results (e.g. "handbook_2024.pdf")
    """
    global reranker
    try:
        # Auto-detect source filter from query if not explicitly passed
        norm_query = normalize_text(query)
        source_keywords = PROFILE["source_keywords"]

        if not source:
            for raw_name in SOURCE_NAMES.keys():
                stem = Path(raw_name).stem
                norm_stem = normalize_text(stem)
                # If query contains the full document name
                if norm_stem in norm_query:
                    source = raw_name
                    break

            # Additional fallback: match short domain keywords against the stem
            if not source:
                for raw_name in SOURCE_NAMES.keys():
                    norm_stem = normalize_text(Path(raw_name).stem)
                    if any(kw in norm_query and kw in norm_stem for kw in source_keywords):
                        source = raw_name
                        break

        # Define filter if source filter is resolved
        q_filter = None
        if source:
            logger.info("Applying Qdrant metadata source filter: %s", source)
            # Stored source values keep the filesystem's Unicode form (macOS
            # ingests NFD, query text arrives NFC) — match both forms so the
            # exact-match filter never silently drops every chunk.
            source_forms = list({
                unicodedata.normalize("NFC", source),
                unicodedata.normalize("NFD", source),
            })
            q_filter = qmodels.Filter(
                must=[
                    qmodels.FieldCondition(
                        key="metadata.source",
                        match=qmodels.MatchAny(any=source_forms)
                    )
                ]
            )

        # 1. Retrieve candidate child chunks for reranking
        results = child_vector_store.similarity_search(query, k=RETRIEVAL_CANDIDATES, filter=q_filter)
        if not results and q_filter is not None:
            # A source filter that matches nothing must not kill retrieval
            logger.warning("Source filter matched no chunks, retrying unfiltered")
            results = child_vector_store.similarity_search(query, k=RETRIEVAL_CANDIDATES)
        if not results:
            return PROFILE["strings"]["retrieval_no_results"]

        # 2. Apply Cross-Encoder Reranking
        if reranker is None:
            with reranker_lock:
                if reranker is None:
                    from sentence_transformers import CrossEncoder
                    logger.info("Loading CrossEncoder reranker model on device: %s...", reranker_device)
                    reranker = CrossEncoder(RERANKER_MODEL, device=reranker_device)

        pairs = [[query, doc.page_content] for doc in results]
        with reranker_lock:
            scores = reranker.predict(pairs, show_progress_bar=False)

        # 3. Sort chunks by reranker score descending
        scored_docs = sorted(zip(results, scores), key=lambda x: x[1], reverse=True)

        # 4. Group unique parent IDs from highest-scoring child chunks
        parent_ids = []
        for doc, score in scored_docs:
            pid = doc.metadata.get('parent_id')
            if pid and pid not in parent_ids:
                parent_ids.append(pid)
                if len(parent_ids) >= limit:
                    break

        contexts = []
        for pid in parent_ids:
            file_name = pid if pid.lower().endswith(".json") else f"{pid}.json"
            path = os.path.join(PARENT_STORE_PATH, file_name)
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                contexts.append(
                    f"Parent ID: {pid}\n"
                    f"File Name: {data.get('metadata', {}).get('source', 'unknown')}\n"
                    f"Content: {data.get('page_content', '').strip()}"
                )

        if not contexts:
            return "NO_PARENT_DOCUMENT"

        return "\n\n".join(contexts)

    except Exception as e:
        return f"RETRIEVAL_ERROR: {e!s}"
