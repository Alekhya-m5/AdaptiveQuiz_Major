"""
Document Ingestion Service for RAG Pipeline.
Coordinates file upload, extraction, chunking, embedding generation, and database persistence.
"""

import json
import logging
import os
import uuid
from typing import Dict, Any, Optional
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.models import db, CourseDocument, DocumentChunk
from app.rag.extractor import extract_document, ExtractionError, EmptyDocumentError, UnsupportedFormatError
from app.rag.chunker import chunk_extracted_units
from app.rag.embeddings import embed_batch

logger = logging.getLogger(__name__)

UPLOAD_FOLDER_DEFAULT = "uploads/materials"


class RAGService:
    """Service handling course material ingestion and lifecycle."""

    @classmethod
    def get_upload_dir(cls) -> str:
        upload_dir = os.path.join(os.getcwd(), UPLOAD_FOLDER_DEFAULT)
        os.makedirs(upload_dir, exist_ok=True)
        return upload_dir

    @classmethod
    def ingest_document(
        cls,
        file_obj: FileStorage,
        title: str,
        course: str = "RM",
        topic: Optional[str] = None,
        teacher_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process and ingest a teacher-uploaded learning material.

        Steps:
        1. Validate file format and size.
        2. Save file safely to disk.
        3. Extract pages/slides.
        4. Structure-aware chunking.
        5. Generate embeddings.
        6. Persist CourseDocument and DocumentChunk records.
        """
        if not file_obj or not file_obj.filename:
            raise ValueError("No file provided.")

        original_filename = secure_filename(file_obj.filename)
        if not original_filename:
            original_filename = f"document_{uuid.uuid4().hex[:8]}.txt"

        clean_title = (title or "").strip() or os.path.splitext(original_filename)[0]
        ext = os.path.splitext(original_filename)[1].lower()

        # Read content bytes
        content_bytes = file_obj.read()
        file_size = len(content_bytes)

        if file_size == 0:
            raise EmptyDocumentError("Uploaded file is empty (0 bytes).")

        # Save to disk
        upload_dir = cls.get_upload_dir()
        unique_disk_name = f"{uuid.uuid4().hex}_{original_filename}"
        disk_path = os.path.join(upload_dir, unique_disk_name)
        with open(disk_path, "wb") as f:
            f.write(content_bytes)

        # Create CourseDocument record in PENDING status
        doc_record = CourseDocument(
            teacher_id=teacher_id,
            title=clean_title,
            filename=original_filename,
            file_type=ext.replace(".", ""),
            file_path=disk_path,
            file_size=file_size,
            course=course or "RM",
            topic=topic,
            status="PROCESSING",
        )
        db.session.add(doc_record)
        db.session.flush()

        try:
            # 1. Extract
            extracted_units = extract_document(content_bytes, original_filename, file_size)

            # 2. Chunk
            chunks_data = chunk_extracted_units(extracted_units)

            if not chunks_data:
                doc_record.status = "EMPTY"
                doc_record.error_message = "No meaningful chunks extracted."
                db.session.commit()
                return {
                    "status": "EMPTY",
                    "document_id": doc_record.id,
                    "filename": original_filename,
                    "total_chunks": 0,
                    "message": "Document contains no usable text chunks.",
                }

            # 3. Generate Embeddings in batch
            chunk_texts = [c["content"] for c in chunks_data]
            embeddings = embed_batch(chunk_texts)

            # 4. Save chunks
            for c_info, emb in zip(chunks_data, embeddings):
                chunk_rec = DocumentChunk(
                    document_id=doc_record.id,
                    chunk_index=c_info["chunk_index"],
                    content=c_info["content"],
                    page_number=c_info.get("page_number"),
                    slide_number=c_info.get("slide_number"),
                    course=course or "RM",
                    topic=topic,
                    concept_tag=topic,  # default concept mapping
                    token_count=c_info.get("token_count", 0),
                    embedding_json=json.dumps(emb),
                )
                db.session.add(chunk_rec)

            doc_record.total_pages_or_slides = len(extracted_units)
            doc_record.total_chunks = len(chunks_data)
            doc_record.status = "PROCESSED"
            db.session.commit()

            return {
                "status": "SUCCESS",
                "document_id": doc_record.id,
                "title": doc_record.title,
                "filename": original_filename,
                "course": doc_record.course,
                "total_pages_or_slides": doc_record.total_pages_or_slides,
                "total_chunks": doc_record.total_chunks,
                "message": f"Successfully processed {doc_record.total_chunks} chunk(s) across {doc_record.total_pages_or_slides} page(s)/slide(s).",
            }

        except Exception as e:
            db.session.rollback()
            doc_record.status = "FAILED"
            doc_record.error_message = str(e)
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
            logger.error(f"Failed to ingest document '{original_filename}': {e}", exc_info=True)
            raise

    @classmethod
    def delete_document(cls, document_id: int) -> bool:
        """Delete document and its associated chunks and disk file."""
        doc = CourseDocument.query.get(document_id)
        if not doc:
            return False

        # Nullify foreign key references in generated_question_records so questions are preserved
        from app.models import GeneratedQuestionRecord
        GeneratedQuestionRecord.query.filter_by(source_document_id=document_id).update(
            {"source_document_id": None}
        )

        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except Exception as e:
                logger.warning(f"Could not remove file {doc.file_path}: {e}")

        db.session.delete(doc)
        db.session.commit()
        return True

