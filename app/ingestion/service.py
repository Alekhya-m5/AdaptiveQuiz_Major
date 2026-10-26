"""
Ingestion Service for managing persistent CSV imports and batch tracking.
"""

from datetime import datetime
import json
import logging
from pathlib import Path

from app.models import db, ImportBatch, ImportedResponse, Student
from app.ingestion.csv_reader import compute_file_hash
from app.ingestion.processor import process_weekly_csv

logger = logging.getLogger(__name__)


class IngestionService:
    """
    Handles file hashing, duplicate prevention, schema validation,
    and transactional persistence of assessment responses into Supabase/PostgreSQL.
    """

    @classmethod
    def import_csv(cls, file_input, filename: str = None) -> dict:
        """
        Import a weekly quiz CSV file into the database.

        Parameters
        ----------
        file_input : str, Path, or file-like object
            Path to CSV file or file buffer.
        filename : str, optional
            Original filename if file_input is a stream.

        Returns
        -------
        dict
            Import summary with batch ID, status, and row statistics.
        """
        if filename is None:
            if isinstance(file_input, (str, Path)):
                filename = Path(file_input).name
            elif hasattr(file_input, "filename"):
                filename = file_input.filename
            else:
                filename = "uploaded_quiz_results.csv"

        file_hash = compute_file_hash(file_input)

        # 1. Duplicate check: has this identical file already been successfully imported?
        existing_batch = ImportBatch.query.filter_by(
            file_hash=file_hash, status="COMPLETED"
        ).first()

        if existing_batch:
            return {
                "status": "DUPLICATE",
                "batch_id": existing_batch.id,
                "filename": filename,
                "file_hash": file_hash,
                "message": (
                    f"File '{filename}' has already been imported in batch #{existing_batch.id} "
                    f"on {existing_batch.completed_at}."
                ),
                "total_rows": existing_batch.total_rows,
                "valid_rows": existing_batch.valid_rows,
                "imported_records": existing_batch.valid_rows,
            }

        # 2. Create batch record in PROCESSING state
        batch = ImportBatch(
            filename=filename,
            file_hash=file_hash,
            status="PROCESSING",
            created_at=datetime.utcnow(),
        )
        db.session.add(batch)
        db.session.flush()

        try:
            # 3. Read and normalize CSV data
            result = process_weekly_csv(file_input)
            records = result["records"]
            batch.total_rows = result["total_rows"]

            # 4. Ensure students exist in students table with precomputed password hash
            # to avoid expensive repeated hashing
            DEFAULT_HASH = "scrypt:32768:8:1$placeholder$hash"
            student_cache = {s.usn: s for s in Student.query.all()}
            new_students = []
            for rec in records:
                usn = rec["student_id"]
                if usn not in student_cache:
                    name_parts = rec["student_name"].split(" ", 1)
                    first_name = name_parts[0] if name_parts else usn
                    last_name = name_parts[1] if len(name_parts) > 1 else ""
                    new_student = Student(
                        usn=usn,
                        first_name=first_name,
                        last_name=last_name,
                        course=rec["subject"] or "RM",
                        password_hash=DEFAULT_HASH,
                    )
                    new_students.append(new_student)
                    student_cache[usn] = new_student

            if new_students:
                db.session.add_all(new_students)
                db.session.flush()

            # 5. Persist imported responses
            # Check existing responses to prevent duplicate insertion
            existing_keys = {
                (r.student_id, r.quiz, r.attempt_number, r.question_id)
                for r in ImportedResponse.query.filter_by(quiz=result["quiz_title"]).all()
            }

            new_responses = []
            for rec in records:
                key = (rec["student_id"], rec["quiz"], rec["attempt_number"], rec["question_id"])
                if key in existing_keys:
                    continue  # idempotent skip

                resp = ImportedResponse(
                    batch_id=batch.id,
                    student_id=rec["student_id"],
                    student_name=rec["student_name"],
                    quiz=rec["quiz"],
                    subject=rec["subject"],
                    attempt_number=rec["attempt_number"],
                    question_no=rec["question_no"],
                    question_id=rec["question_id"],
                    concept_tag=rec["concept_tag"],
                    bloom_level=rec["bloom_level"],
                    question_text=rec["question_text"],
                    option_a=rec["option_a"],
                    option_b=rec["option_b"],
                    option_c=rec["option_c"],
                    option_d=rec["option_d"],
                    selected_option=rec["selected_option"],
                    correct_option=rec["correct_option"],
                    is_correct=rec["is_correct"],
                    is_unanswered=rec["is_unanswered"],
                    question_marks=rec["question_marks"],
                    marks_obtained=rec["marks_obtained"],
                    submitted_at=rec["submitted_at"],
                    source="imported_csv",
                )
                new_responses.append(resp)
                existing_keys.add(key)

            if new_responses:
                db.session.add_all(new_responses)

            batch.valid_rows = len(records)
            batch.status = "COMPLETED"
            batch.completed_at = datetime.utcnow()
            batch.error_summary = json.dumps({
                "warnings": result["warnings"],
                "inserted_new": len(new_responses),
                "skipped_existing": len(records) - len(new_responses),
            })

            db.session.commit()

            return {
                "status": "SUCCESS",
                "batch_id": batch.id,
                "filename": filename,
                "file_hash": file_hash,
                "total_rows": batch.total_rows,
                "valid_rows": batch.valid_rows,
                "inserted_records": len(new_responses),
                "student_count": result["student_count"],
                "question_count": result["question_count"],
                "quiz_title": result["quiz_title"],
                "warnings": result["warnings"],
            }

        except Exception as e:
            db.session.rollback()
            logger.exception(f"Import failed for '{filename}': {e}")
            # Mark batch as FAILED
            try:
                failed_batch = ImportBatch(
                    filename=filename,
                    file_hash=file_hash,
                    status="FAILED",
                    total_rows=0,
                    error_summary=str(e),
                    completed_at=datetime.utcnow(),
                )
                db.session.add(failed_batch)
                db.session.commit()
            except Exception as inner_e:
                db.session.rollback()
                logger.error(f"Could not record failed batch: {inner_e}")

            raise ValueError(f"Import failed: {e}")
