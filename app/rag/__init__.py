"""
RAG (Retrieval-Augmented Generation) package for course learning materials.
"""

from app.rag.extractor import extract_document, ExtractionError, EmptyDocumentError, UnsupportedFormatError
from app.rag.chunker import chunk_extracted_units
from app.rag.embeddings import embed_text, embed_batch, cosine_similarity
from app.rag.retriever import retrieve_context
from app.rag.service import RAGService

__all__ = [
    "extract_document",
    "ExtractionError",
    "EmptyDocumentError",
    "UnsupportedFormatError",
    "chunk_extracted_units",
    "embed_text",
    "embed_batch",
    "cosine_similarity",
    "retrieve_context",
    "RAGService",
]

