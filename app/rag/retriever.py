"""
RAG Semantic Retrieval Engine.
Retrieves relevant course material chunks filtered by course and concept,
ranked by dense semantic similarity and keyword grounding.
"""

import json
import logging
from typing import Dict, Any, List, Optional
from app.models import db, CourseDocument, DocumentChunk
from app.rag.embeddings import embed_text, cosine_similarity

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 3
DEFAULT_SIMILARITY_THRESHOLD = 0.12


def build_source_ref(chunk: DocumentChunk, doc_filename: Optional[str] = None) -> str:
    """Generate human-readable citation reference for question traceability."""
    name = doc_filename or (chunk.document.filename if chunk.document else "Document")
    if chunk.slide_number is not None:
        return f"{name} (Slide {chunk.slide_number})"
    elif chunk.page_number is not None:
        return f"{name} (Page {chunk.page_number})"
    return f"{name} (Chunk #{chunk.chunk_index + 1})"


def retrieve_context(
    course: str,
    concept_tag: Optional[str] = None,
    topic: Optional[str] = None,
    query_text: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    document_id: Optional[int] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
    chunk_offset: int = 0,
) -> Dict[str, Any]:
    """
    Retrieve top-k relevant course material chunks.

    Parameters
    ----------
    course : str
        Target subject/course code (e.g. 'RM', 'RM and IPR').
    concept_tag : str, optional
        Target learning concept.
    topic : str, optional
        Broad topic area.
    query_text : str, optional
        Custom question query. If omitted, constructed from concept_tag and topic.
    top_k : int, default 3
        Maximum number of relevant chunks to return.
    similarity_threshold : float, default 0.12
        Minimum cosine similarity score required for inclusion.
    document_id : int, optional
        Filter chunks from a specific document.

    Returns
    -------
    Dict[str, Any]
        Structured retrieval result containing found flag, chunks, and metadata.
    """
    # Formulate query
    query_parts = []
    if concept_tag:
        query_parts.append(concept_tag)
    if topic and topic != concept_tag:
        query_parts.append(topic)
    if query_text:
        query_parts.append(query_text)

    combined_query = " - ".join(query_parts) if query_parts else (course or "Course Content")
    query_embedding = embed_text(combined_query)

    # Base query for chunks
    query = DocumentChunk.query.join(CourseDocument, DocumentChunk.document_id == CourseDocument.id)

    # Filter by course: accept exact or flexible match (e.g. 'RM' in 'RM and IPR')
    if course:
        clean_course = course.strip()
        query = query.filter(
            (DocumentChunk.course.ilike(f"%{clean_course}%")) |
            (CourseDocument.course.ilike(f"%{clean_course}%"))
        )

    if document_id:
        query = query.filter(DocumentChunk.document_id == document_id)

    candidate_chunks = query.all()

    if not candidate_chunks:
        return {
            "found": False,
            "query": combined_query,
            "course": course,
            "concept_tag": concept_tag,
            "chunks": [],
            "combined_context": "",
            "message": f"No course learning materials found in database for course '{course}'.",
        }

    scored_chunks = []

    for chunk in candidate_chunks:
        chunk_embedding = None
        if chunk.embedding_json:
            try:
                chunk_embedding = json.loads(chunk.embedding_json)
            except Exception:
                chunk_embedding = None

        if not chunk_embedding:
            chunk_embedding = embed_text(chunk.content)
            # Cache it
            try:
                chunk.embedding_json = json.dumps(chunk_embedding)
                db.session.add(chunk)
            except Exception:
                pass

        base_sim = cosine_similarity(query_embedding, chunk_embedding)

        # Keyword alignment boost: if chunk explicitly mentions the target concept, boost relevance
        boost = 0.0
        content_lower = chunk.content.lower()
        if concept_tag and concept_tag.lower() in content_lower:
            boost += 0.15
        if topic and topic.lower() in content_lower:
            boost += 0.10
        if chunk.concept_tag and concept_tag and chunk.concept_tag.lower() == concept_tag.lower():
            boost += 0.20

        final_score = round(base_sim + boost, 4)

        if final_score >= similarity_threshold:
            scored_chunks.append((final_score, chunk))

    # Sort descending by final relevance score
    scored_chunks.sort(key=lambda x: x[0], reverse=True)

    # Context Diversity: filter or rotate chunks when requested to avoid repeating identical text
    exclude_set = set(exclude_chunk_ids or [])
    non_excluded = [sc for sc in scored_chunks if sc[1].id not in exclude_set]

    if len(non_excluded) >= top_k:
        offset = (chunk_offset % max(1, len(non_excluded) - top_k + 1)) if chunk_offset > 0 else 0
        top_results = non_excluded[offset : offset + top_k]
    elif non_excluded:
        # Prioritize unused chunks, then backfill from previously used chunks to reach top_k
        top_results = list(non_excluded)
        needed = top_k - len(top_results)
        backfill = [sc for sc in scored_chunks if sc[1].id in exclude_set][:needed]
        top_results.extend(backfill)
    else:
        # All available chunks were excluded; rotate using chunk_offset across scored chunks
        if chunk_offset > 0 and len(scored_chunks) > top_k:
            offset = chunk_offset % (len(scored_chunks) - top_k + 1)
            top_results = scored_chunks[offset : offset + top_k]
        else:
            top_results = scored_chunks[:top_k]

    if not top_results:
        return {
            "found": False,
            "query": combined_query,
            "course": course,
            "concept_tag": concept_tag,
            "chunks": [],
            "combined_context": "",
            "message": f"No chunks met similarity threshold ({similarity_threshold}) for '{combined_query}'.",
        }

    formatted_chunks = []
    for score, chunk in top_results:
        doc_filename = chunk.document.filename if chunk.document else "Document"
        source_ref = build_source_ref(chunk, doc_filename)
        formatted_chunks.append({
            "chunk_id": chunk.id,
            "document_id": chunk.document_id,
            "filename": doc_filename,
            "page_number": chunk.page_number,
            "slide_number": chunk.slide_number,
            "concept_tag": chunk.concept_tag,
            "content": chunk.content,
            "similarity_score": score,
            "source_ref": source_ref,
        })

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    return {
        "found": True,
        "query": combined_query,
        "course": course,
        "concept_tag": concept_tag,
        "chunks": formatted_chunks,
        "combined_context": "\n\n".join(c["content"] for c in formatted_chunks),
        "message": f"Retrieved {len(formatted_chunks)} relevant context chunk(s).",
    }
