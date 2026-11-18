"""
Strict Output Validation and Quality Control for Generated MCQs.
Enforces schema conformance, option uniqueness, answer key consistency,
and source grounding before questions are shown to students or saved.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

REQUIRED_KEYS = {"question", "option_a", "option_b", "option_c", "option_d", "correct_option", "explanation"}
VALID_OPTIONS = {"A", "B", "C", "D"}


def strip_thinking_tokens(raw_output: str) -> str:
    """Strip any <think>...</think> reasoning blocks or im_start/end tags."""
    if not raw_output:
        return ""
    # Strip <think>...</think> content
    cleaned = re.sub(r"<think>.*?</think>", "", raw_output, flags=re.DOTALL)
    # Strip chat template tags
    cleaned = re.sub(r"<\|im_start\|>.*?<\|im_end\|>", "", cleaned)
    cleaned = cleaned.replace("<|im_start|>", "").replace("<|im_end|>", "")
    return cleaned.strip()


def parse_json_from_model_output(raw_output: str) -> Optional[Dict[str, Any]]:
    """
    Robustly extract and parse JSON object from LLM response.
    Handles markdown code blocks (```json ... ```) and leading/trailing text.
    """
    if not raw_output:
        return None

    cleaned = strip_thinking_tokens(raw_output)

    # 1. Try direct json.loads
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Try fenced json block ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 3. Find outermost curly braces
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = cleaned[start:end+1]
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return None


def validate_mcq_data(
    data: Any,
    source_context: Optional[str] = None
) -> Tuple[bool, List[str], Dict[str, Any]]:
    """
    Validate structured MCQ data against educational and technical constraints.

    Returns
    -------
    Tuple[bool, List[str], Dict[str, Any]]
        (is_valid, list_of_errors, normalized_data)
    """
    errors = []
    if not isinstance(data, dict):
        return False, ["Model output is not a JSON object."], {}

    # Handle alternate key styles (e.g. options: {"A":..., "B":...})
    normalized = {}
    
    # Question text
    q_text = data.get("question") or data.get("question_text", "")
    normalized["question"] = str(q_text).strip() if q_text else ""
    if not normalized["question"]:
        errors.append("Question text is empty or missing.")

    # Options
    options_nested = data.get("options")
    if isinstance(options_nested, dict):
        normalized["option_a"] = str(options_nested.get("A", "")).strip()
        normalized["option_b"] = str(options_nested.get("B", "")).strip()
        normalized["option_c"] = str(options_nested.get("C", "")).strip()
        normalized["option_d"] = str(options_nested.get("D", "")).strip()
    else:
        normalized["option_a"] = str(data.get("option_a", "")).strip()
        normalized["option_b"] = str(data.get("option_b", "")).strip()
        normalized["option_c"] = str(data.get("option_c", "")).strip()
        normalized["option_d"] = str(data.get("option_d", "")).strip()

    # Check non-empty options
    opts = [normalized["option_a"], normalized["option_b"], normalized["option_c"], normalized["option_d"]]
    for idx, opt in enumerate(["A", "B", "C", "D"]):
        if not opts[idx]:
            errors.append(f"Option {opt} is empty.")

    # Check duplicate options
    clean_opts_lower = [o.lower() for o in opts if o]
    if len(clean_opts_lower) != len(set(clean_opts_lower)):
        errors.append("Options contain duplicate text.")

    # Correct Option
    raw_correct = str(data.get("correct_option") or data.get("correct_answer") or "").strip().upper()
    if raw_correct.startswith("OPTION "):
        raw_correct = raw_correct.replace("OPTION ", "").strip()
    if raw_correct not in VALID_OPTIONS:
        errors.append(f"Correct answer '{raw_correct}' is not one of 'A', 'B', 'C', 'D'.")
    normalized["correct_option"] = raw_correct

    # Explanation
    expl = str(data.get("explanation", "")).strip()
    if not expl:
        errors.append("Explanation is missing or empty.")
    normalized["explanation"] = expl

    # Metadata passthroughs
    normalized["difficulty"] = data.get("difficulty", "Medium")
    normalized["bloom_level"] = data.get("bloom_level", "Understand")

    # Grounding check: verify that at least one key term from the question or correct option
    # is substantiated by the retrieved context
    grounded = True
    if source_context and normalized["question"]:
        ctx_lower = source_context.lower()
        q_words = [w.lower() for w in re.findall(r"\b\w{4,}\b", normalized["question"])]
        common_words = {"which", "what", "where", "following", "statement", "about", "choose", "correct"}
        content_words = [w for w in q_words if w not in common_words]
        if content_words:
            matched_words = [w for w in content_words if w in ctx_lower]
            if not matched_words:
                # Question has zero vocabulary in common with source
                grounded = False
                errors.append("Question does not appear sufficiently grounded in the retrieved context.")

    is_valid = len(errors) == 0
    return is_valid, errors, normalized
