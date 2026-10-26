"""
Data normalization, parsing, and validation for imported quiz responses.
"""

from datetime import datetime
import logging
import math
import pandas as pd
from app.ingestion.csv_reader import read_weekly_csv

logger = logging.getLogger(__name__)

UNANSWERED_TOKENS = {"not answered", "unanswered", "none", "nan", "null", ""}


def normalize_option(value):
    """
    Normalize option strings.
    Returns uppercase option letter (e.g. 'A', 'B', 'C', 'D') or None if unanswered/blank.
    """
    if value is None:
        return None
    val_str = str(value).strip()
    if not val_str or val_str.lower() in UNANSWERED_TOKENS:
        return None
    return val_str.upper()


def parse_timestamp(value):
    """
    Convert CSV timestamp into Python datetime.
    Supports formats:
    - '02/09/2026 8:40:12 AM'  (%d/%m/%Y %I:%M:%S %p)
    - '02-09-2026 08:40'       (%d-%m-%Y %H:%M)
    - '2026-09-02 08:40:12'    (%Y-%m-%d %H:%M:%S)
    - ISO / standard pandas fallback
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None

    if isinstance(value, datetime):
        return value

    val_str = str(value).strip()
    if not val_str:
        return None

    formats = [
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(val_str, fmt)
        except ValueError:
            continue

    try:
        ts = pd.to_datetime(val_str)
        return ts.to_pydatetime()
    except Exception:
        logger.warning(f"Failed to parse timestamp '{val_str}'")
        return None


def build_question_catalog(df: pd.DataFrame) -> dict:
    """
    Build a catalog of canonical question metadata by question_id.
    Ensures that if some attempts had missing Bloom's Level or option texts,
    they are backfilled from other occurrences of the same Question ID.
    """
    catalog = {}
    for _, row in df.iterrows():
        qid = str(row.get("Question ID", "")).strip()
        if not qid:
            continue

        if qid not in catalog:
            catalog[qid] = {
                "question_text": None,
                "concept_tag": None,
                "bloom_level": None,
                "option_a": None,
                "option_b": None,
                "option_c": None,
                "option_d": None,
                "correct_option": None,
            }

        entry = catalog[qid]

        bloom = row.get("Bloom's Level")
        if pd.notna(bloom) and str(bloom).strip() and entry["bloom_level"] is None:
            entry["bloom_level"] = str(bloom).strip()

        concept = row.get("Concept Tag")
        if pd.notna(concept) and str(concept).strip() and entry["concept_tag"] is None:
            entry["concept_tag"] = str(concept).strip()

        qtext = row.get("Question")
        if pd.notna(qtext) and str(qtext).strip() and entry["question_text"] is None:
            entry["question_text"] = str(qtext).strip()

        for opt_col, key in [
            ("Option A", "option_a"),
            ("Option B", "option_b"),
            ("Option C", "option_c"),
            ("Option D", "option_d"),
        ]:
            opt_val = row.get(opt_col)
            if pd.notna(opt_val) and str(opt_val).strip() and entry[key] is None:
                entry[key] = str(opt_val).strip()

        corr = row.get("Correct Option")
        if pd.notna(corr) and str(corr).strip() and entry["correct_option"] is None:
            entry["correct_option"] = normalize_option(corr)

    return catalog


def normalize_response(row, question_catalog=None, row_index=0):
    """
    Convert one CSV row into a clean response record.
    Returns (record, inconsistency_warning).
    """
    warning = None

    qid = str(row.get("Question ID", "")).strip()
    if not qid:
        raise ValueError(f"Row {row_index}: Missing Question ID")

    catalog_meta = question_catalog.get(qid, {}) if question_catalog else {}

    raw_selected = row.get("Selected Option")
    selected_option = normalize_option(raw_selected)

    raw_correct = row.get("Correct Option")
    correct_option = normalize_option(raw_correct) or catalog_meta.get("correct_option")
    if not correct_option:
        raise ValueError(f"Row {row_index}: Missing Correct Option for Question ID {qid}")

    # Determine unanswered status
    is_unanswered = (selected_option is None)

    # Derive is_correct strictly from selected vs correct
    if is_unanswered:
        is_correct = False
    else:
        is_correct = (selected_option == correct_option)

    # Cross-check against Marks Obtained
    try:
        marks_obtained = float(row.get("Marks Obtained", 0.0))
    except (ValueError, TypeError):
        marks_obtained = 0.0

    try:
        question_marks = float(row.get("Question Marks", 1.0))
    except (ValueError, TypeError):
        question_marks = 1.0

    expected_marks = question_marks if is_correct else 0.0
    if abs(expected_marks - marks_obtained) > 1e-5:
        usn = str(row.get("Student ID / USN", "")).strip()
        warning = (
            f"Row {row_index} inconsistency for USN '{usn}', Question '{qid}': "
            f"selected='{selected_option}', correct='{correct_option}', derived_is_correct={is_correct} "
            f"(expected marks {expected_marks}), but CSV Marks Obtained = {marks_obtained}"
        )
        logger.warning(warning)

    # Parse timestamp
    submitted_at = parse_timestamp(row.get("Submitted At"))
    if submitted_at is None:
        raise ValueError(f"Row {row_index}: Invalid Submitted At timestamp '{row.get('Submitted At')}'")

    # Bloom's level and concept tag with fallback to catalog
    raw_bloom = row.get("Bloom's Level")
    bloom_level = (
        str(raw_bloom).strip()
        if pd.notna(raw_bloom) and str(raw_bloom).strip()
        else catalog_meta.get("bloom_level")
    )

    raw_concept = row.get("Concept Tag")
    concept_tag = (
        str(raw_concept).strip()
        if pd.notna(raw_concept) and str(raw_concept).strip()
        else catalog_meta.get("concept_tag")
    )
    if not concept_tag:
        raise ValueError(f"Row {row_index}: Missing Concept Tag for Question ID {qid}")

    # Question text and options
    def get_text_field(col_name, catalog_key):
        val = row.get(col_name)
        if pd.notna(val) and str(val).strip():
            return str(val).strip()
        return catalog_meta.get(catalog_key)

    record = {
        "student_id": str(row.get("Student ID / USN", "")).strip(),
        "student_name": str(row.get("Student Name", "")).strip(),
        "quiz": str(row.get("Quiz", "")).strip(),
        "subject": str(row.get("Subject", "")).strip(),
        "attempt_number": int(row.get("Attempt", 1)),
        "question_no": int(row.get("Question No", 0)),
        "question_id": qid,
        "concept_tag": concept_tag,
        "bloom_level": bloom_level,
        "question_text": get_text_field("Question", "question_text"),
        "option_a": get_text_field("Option A", "option_a"),
        "option_b": get_text_field("Option B", "option_b"),
        "option_c": get_text_field("Option C", "option_c"),
        "option_d": get_text_field("Option D", "option_d"),
        "selected_option": selected_option,
        "correct_option": correct_option,
        "is_correct": is_correct,
        "is_unanswered": is_unanswered,
        "question_marks": question_marks,
        "marks_obtained": marks_obtained,
        "submitted_at": submitted_at,
        "source": "imported_csv",
    }

    return record, warning


def process_weekly_csv(file_input):
    """
    Read, validate, and normalize weekly assessment CSV.

    Returns
    -------
    dict
        {
            "records": list of dicts,
            "warnings": list of inconsistency warnings,
            "total_rows": int,
            "valid_rows": int,
            "student_count": int,
            "question_count": int,
            "quiz_title": str
        }
    """
    df = read_weekly_csv(file_input)
    catalog = build_question_catalog(df)

    records = []
    warnings = []

    for idx, row in df.iterrows():
        record, warn = normalize_response(row, question_catalog=catalog, row_index=idx + 1)
        records.append(record)
        if warn:
            warnings.append(warn)

    students = {r["student_id"] for r in records}
    questions = {r["question_id"] for r in records}
    quiz_title = records[0]["quiz"] if records else ""

    return {
        "records": records,
        "warnings": warnings,
        "total_rows": len(df),
        "valid_rows": len(records),
        "student_count": len(students),
        "question_count": len(questions),
        "quiz_title": quiz_title,
    }