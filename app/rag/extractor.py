"""
Document text extraction service for RAG pipeline.
Extracts structured, slide/page-aware text from PDF, PPTX, and TXT files.
"""

import io
import os
import re
from typing import List, Dict, Any, Union
import logging

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".ppt", ".txt", ".md"}
MAX_FILE_SIZE = 32 * 1024 * 1024  # 32 MB


class ExtractionError(Exception):
    """Custom exception raised when document extraction fails."""
    pass


class EmptyDocumentError(ExtractionError):
    """Raised when document contains no extractable text."""
    pass


class UnsupportedFormatError(ExtractionError):
    """Raised when document format is not supported."""
    pass


def clean_text(text: str) -> str:
    """Clean extracted text while preserving educational punctuation and paragraph breaks."""
    if not text:
        return ""
    # Strip null bytes and unusual control characters
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    # Normalize multiple blank lines to double newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Normalize horizontal whitespace
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def validate_file(filename: str, file_size: int = 0) -> str:
    """Validate filename extension and optional size constraint."""
    if not filename:
        raise ValueError("Filename is required.")
    
    ext = os.path.splitext(filename)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFormatError(
            f"Unsupported file format '{ext}'. Allowed formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    
    if file_size > MAX_FILE_SIZE:
        raise ValueError(
            f"File size ({file_size / (1024*1024):.1f} MB) exceeds maximum allowed size ({MAX_FILE_SIZE / (1024*1024):.0f} MB)."
        )
    
    return ext


def extract_from_pdf(file_input: Union[str, bytes, io.BytesIO]) -> List[Dict[str, Any]]:
    """
    Extract text page-by-page from a PDF document.
    Returns list of page items: [{"page_number": int, "text": str}]
    """
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise ImportError("pypdf is required for PDF extraction. Install via 'pip install pypdf'.")

    stream = file_input if isinstance(file_input, io.BytesIO) else (
        io.BytesIO(file_input) if isinstance(file_input, bytes) else open(file_input, "rb")
    )
    should_close = not isinstance(file_input, (io.BytesIO, bytes))

    try:
        reader = PdfReader(stream)
        if len(reader.pages) == 0:
            raise EmptyDocumentError("PDF file has 0 pages.")

        pages_data = []
        total_text_len = 0

        for idx, page in enumerate(reader.pages):
            page_num = idx + 1
            try:
                page_text = page.extract_text() or ""
            except Exception as e:
                logger.warning(f"Error extracting page {page_num}: {e}")
                page_text = ""

            cleaned = clean_text(page_text)
            total_text_len += len(cleaned)
            pages_data.append({
                "page_number": page_num,
                "slide_number": None,
                "text": cleaned,
            })

        if total_text_len == 0:
            raise EmptyDocumentError("PDF document contains no readable text (it may be scanned/image-only or empty).")

        return pages_data
    except PdfReadError as e:
        raise ExtractionError(f"Corrupted or invalid PDF file: {e}")
    except EmptyDocumentError:
        raise
    except Exception as e:
        raise ExtractionError(f"Failed to read PDF: {e}")
    finally:
        if should_close:
            stream.close()


def extract_from_pptx(file_input: Union[str, bytes, io.BytesIO]) -> List[Dict[str, Any]]:
    """
    Extract text slide-by-slide from a PPTX presentation.
    Extracts text shapes, table cells, and speaker notes.
    Returns list of slide items: [{"slide_number": int, "text": str}]
    """
    try:
        from pptx import Presentation
    except ImportError:
        raise ImportError("python-pptx is required for PPTX extraction. Install via 'pip install python-pptx'.")

    stream = file_input if isinstance(file_input, io.BytesIO) else (
        io.BytesIO(file_input) if isinstance(file_input, bytes) else open(file_input, "rb")
    )
    should_close = not isinstance(file_input, (io.BytesIO, bytes))

    try:
        prs = Presentation(stream)
        if len(prs.slides) == 0:
            raise EmptyDocumentError("Presentation contains 0 slides.")

        slides_data = []
        total_text_len = 0

        for idx, slide in enumerate(prs.slides):
            slide_num = idx + 1
            text_parts = []

            # Extract title if present
            if slide.shapes.title and slide.shapes.title.text:
                title_text = clean_text(slide.shapes.title.text)
                if title_text:
                    text_parts.append(f"Title: {title_text}")

            for shape in slide.shapes:
                if shape == slide.shapes.title:
                    continue

                # Standard text frame
                if shape.has_text_frame:
                    shape_text = "\n".join(
                        p.text.strip() for p in shape.text_frame.paragraphs if p.text.strip()
                    )
                    cleaned = clean_text(shape_text)
                    if cleaned:
                        text_parts.append(cleaned)

                # Tables
                elif shape.has_table:
                    table_rows = []
                    for row in shape.table.rows:
                        row_vals = [clean_text(cell.text) for cell in row.cells if clean_text(cell.text)]
                        if row_vals:
                            table_rows.append(" | ".join(row_vals))
                    if table_rows:
                        text_parts.append("\n".join(table_rows))

            # Slide Notes
            if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                notes = clean_text(slide.notes_slide.notes_text_frame.text)
                if notes and not notes.startswith("Slide"):
                    text_parts.append(f"Notes: {notes}")

            combined_slide_text = clean_text("\n\n".join(text_parts))
            total_text_len += len(combined_slide_text)
            slides_data.append({
                "page_number": None,
                "slide_number": slide_num,
                "text": combined_slide_text,
            })

        if total_text_len == 0:
            raise EmptyDocumentError("Presentation contains no readable text content.")

        return slides_data
    except EmptyDocumentError:
        raise
    except Exception as e:
        raise ExtractionError(f"Corrupted or invalid PPTX file: {e}")
    finally:
        if should_close:
            stream.close()


def extract_from_txt(file_input: Union[str, bytes, io.BytesIO]) -> List[Dict[str, Any]]:
    """Extract text from plain text or markdown document."""
    if isinstance(file_input, (bytes, io.BytesIO)):
        raw_bytes = file_input.getvalue() if isinstance(file_input, io.BytesIO) else file_input
        try:
            content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = raw_bytes.decode("latin-1", errors="replace")
    else:
        with open(file_input, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

    cleaned = clean_text(content)
    if not cleaned:
        raise EmptyDocumentError("Text document is empty.")

    return [{
        "page_number": 1,
        "slide_number": None,
        "text": cleaned,
    }]


def extract_document(
    file_input: Union[str, bytes, io.BytesIO],
    filename: str,
    file_size: int = 0
) -> List[Dict[str, Any]]:
    """
    Unified entry point for document extraction.
    Dispatches to appropriate parser based on file extension.
    """
    ext = validate_file(filename, file_size)

    if ext == ".pdf":
        return extract_from_pdf(file_input)
    elif ext in {".pptx", ".ppt"}:
        return extract_from_pptx(file_input)
    elif ext in {".txt", ".md"}:
        return extract_from_txt(file_input)
    else:
        raise UnsupportedFormatError(f"Unsupported extension: {ext}")

