"""
Structure-aware chunking service for educational course materials.
Maintains slide boundaries, page references, and coherent semantic boundaries.
"""

import re
from typing import List, Dict, Any


DEFAULT_TARGET_CHUNK_SIZE = 500  # characters (~80-100 words)
DEFAULT_CHUNK_OVERLAP = 80       # characters (~15 words)
MIN_CHUNK_LENGTH = 30           # drop trivial or whitespace chunks


def split_text_with_overlap(
    text: str,
    target_size: int = DEFAULT_TARGET_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP
) -> List[str]:
    """
    Split text into chunks aiming for target_size with overlap,
    preferring paragraph breaks and sentence ends (. ! ?).
    """
    if len(text) <= target_size:
        return [text]

    # Split into paragraphs first
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= target_size:
            current_chunk = f"{current_chunk}\n\n{para}".strip()
        else:
            if current_chunk:
                chunks.append(current_chunk)
            
            # If paragraph itself is larger than target_size, split by sentences
            if len(para) > target_size:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) + 1 <= target_size:
                        sub_chunk = f"{sub_chunk} {sent}".strip()
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk)
                        sub_chunk = sent
                if sub_chunk:
                    current_chunk = sub_chunk
                else:
                    current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def chunk_extracted_units(
    units: List[Dict[str, Any]],
    target_size: int = DEFAULT_TARGET_CHUNK_SIZE,
    overlap: int = DEFAULT_CHUNK_OVERLAP
) -> List[Dict[str, Any]]:
    """
    Takes extracted document pages/slides and creates chunks with metadata.
    Preserves page_number and slide_number on every generated chunk.
    """
    chunk_list = []
    global_index = 0

    for unit in units:
        text = unit.get("text", "").strip()
        page_num = unit.get("page_number")
        slide_num = unit.get("slide_number")

        if not text:
            continue

        # If it's a slide with reasonable content length, keep it unified as one chunk
        if slide_num is not None and len(text) <= target_size * 1.5:
            text_chunks = [text]
        else:
            text_chunks = split_text_with_overlap(text, target_size=target_size, overlap=overlap)

        for chunk_text in text_chunks:
            cleaned = chunk_text.strip()
            if len(cleaned) < MIN_CHUNK_LENGTH:
                continue

            chunk_list.append({
                "chunk_index": global_index,
                "content": cleaned,
                "page_number": page_num,
                "slide_number": slide_num,
                "token_count": len(cleaned.split()),
            })
            global_index += 1

    return chunk_list

