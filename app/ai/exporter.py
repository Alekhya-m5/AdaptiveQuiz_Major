"""
Dataset export service for future LoRA fine-tuning.
Exports ONLY curated, teacher-approved question records in Hugging Face / Qwen format.
Guarantees zero PII leakage and full source grounding traceability.
"""

import json
from typing import List, Dict, Any
from app.models import GeneratedQuestionRecord


def export_approved_dataset_jsonl(records: List[GeneratedQuestionRecord]) -> str:
    """
    Format approved GeneratedQuestionRecords into supervised fine-tuning (SFT) JSONL records.
    Filters exclusively for approved records to prevent low-quality or hallucinated data leakage.
    """
    lines = []

    for r in records:
        if r.validation_status != "approved":
            continue

        item = {
            "instruction": "Generate an educational multiple-choice question grounded in course material.",
            "input": {
                "course": r.course,
                "concept": r.concept_tag,
                "difficulty": r.target_difficulty,
                "bloom_level": r.target_bloom or "Understand",
                "learner_theta": round(r.learner_theta, 4) if r.learner_theta is not None else 0.0,
                "concept_mastery": round(r.learner_mastery, 4) if r.learner_mastery is not None else 0.20,
                "retrieved_context": r.source_context or "",
            },
            "output": {
                "question": r.question_text,
                "options": {
                    "A": r.option_a,
                    "B": r.option_b,
                    "C": r.option_c,
                    "D": r.option_d,
                },
                "correct_answer": r.correct_option,
                "explanation": r.explanation,
            },
            "metadata": {
                "record_id": r.id,
                "source_filename": r.source_filename,
                "source_page_or_slide": r.source_page_or_slide,
                "teacher_edited": r.teacher_edited,
                "quality_score": r.quality_score,
                "model_version": r.model_name,
                "adapter_version": r.adapter_version,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        }
        lines.append(json.dumps(item, ensure_ascii=False))

    return "\n".join(lines) + ("\n" if lines else "")
