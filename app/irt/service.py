"""
IRT Service.

Integrates Rasch (1PL) Item Calibration and Student Ability Estimation
with PostgreSQL/Supabase persistence.
"""

from collections import defaultdict
from datetime import datetime
import json
import logging
from typing import Dict, Any, Optional

from app.models import (
    db,
    ImportedResponse,
    ItemCalibration,
    StudentTheta,
    Student,
    Attempt,
    Question,
    ThetaHistory,
)
from app.irt.calibration import calibrate_items, estimate_theta_mle
from app.irt.theta import update_theta

logger = logging.getLogger(__name__)


class IRTService:
    """
    Production IRT Service for Rasch / 1PL Calibration and Estimation.
    Restricted exclusively to validated imported assessment records.
    """

    @classmethod
    def recalculate_all(
        cls,
        quiz_filter: Optional[str] = None,
        item_scope: str = "first_attempt",
        min_responses: int = 5,
    ) -> Dict[str, Any]:
        """
        Batch calibration of item difficulties and estimation of student ability (theta)
        using only verified external imported assessment responses.

        Parameters
        ----------
        quiz_filter : str, optional
            Limit to a specific quiz title, or None for all.
        item_scope : str, default 'first_attempt'
            'first_attempt' (standard psychometric practice to avoid practice effects)
            or 'all_attempts'.
        min_responses : int, default 5
            Minimum responses required to calibrate an item.

        Returns
        -------
        Dict[str, Any]
            Calibration and estimation summary.
        """
        # Query ONLY imported assessment records (source == 'imported_csv')
        # Dummy or manual web responses are explicitly excluded.
        query = ImportedResponse.query.filter_by(source="imported_csv")
        if quiz_filter:
            query = query.filter_by(quiz=quiz_filter)

        all_records = query.order_by(
            ImportedResponse.submitted_at.asc(),
            ImportedResponse.attempt_number.asc(),
            ImportedResponse.question_no.asc()
        ).all()

        if not all_records:
            return {
                "status": "NO_DATA",
                "message": "No imported assessment records found for IRT calculation.",
                "items_calibrated": 0,
                "students_estimated": 0,
            }

        records_dicts = [
            {
                "student_id": r.student_id,
                "quiz": r.quiz,
                "attempt_number": r.attempt_number,
                "question_id": r.question_id,
                "concept_tag": r.concept_tag,
                "bloom_level": r.bloom_level,
                "is_correct": r.is_correct,
                "is_unanswered": r.is_unanswered,
                "submitted_at": r.submitted_at,
            }
            for r in all_records
        ]

        # ----------------------------------------------------
        # 1. Calibrate Item Difficulties
        # ----------------------------------------------------
        if item_scope == "first_attempt":
            calib_records = [r for r in records_dicts if r["attempt_number"] == 1]
            if not calib_records:
                calib_records = records_dicts
        else:
            calib_records = records_dicts

        calibrations = calibrate_items(
            calib_records, min_responses=min_responses, method="PROX"
        )

        existing_items = {ic.question_id: ic for ic in ItemCalibration.query.all()}
        for qid, cal in calibrations.items():
            item_record = existing_items.get(qid)
            if not item_record:
                item_record = ItemCalibration(question_id=qid)
                db.session.add(item_record)
                existing_items[qid] = item_record

            item_record.quiz = cal.get("quiz")
            item_record.concept_tag = cal.get("concept_tag")
            item_record.bloom_level = cal.get("bloom_level")
            item_record.total_responses = cal["total"]
            item_record.correct_responses = cal["correct"]
            item_record.unanswered_responses = cal["unanswered"]
            item_record.proportion_correct = cal["proportion_correct"]
            item_record.difficulty_b = cal["difficulty_b"]
            item_record.std_error = cal["std_error"]
            item_record.calibration_status = cal["calibration_status"]
            item_record.calibration_method = cal["calibration_method"]
            item_record.metadata_json = json.dumps({
                "calibrated_from": item_scope,
                "notes": cal.get("notes"),
                "timestamp": datetime.utcnow().isoformat(),
            })
            item_record.updated_at = datetime.utcnow()

        db.session.flush()

        # Build difficulty lookup map
        diff_map = {
            qid: cal["difficulty_b"]
            for qid, cal in calibrations.items()
        }

        # ----------------------------------------------------
        # 2. Estimate Student Ability (Theta)
        # ----------------------------------------------------
        # Group records by student
        student_records = defaultdict(list)
        for r in records_dicts:
            student_records[r["student_id"]].append(r)

        student_cache = {s.usn: s for s in Student.query.all()}
        existing_thetas = {st.usn: st for st in StudentTheta.query.all()}

        student_estimates = {}
        for usn, s_recs in student_records.items():
            # Ensure student exists in students table
            if usn not in student_cache:
                student = Student(usn=usn, first_name=usn, last_name="", course="RM", password_hash="placeholder")
                db.session.add(student)
                student_cache[usn] = student

            # Ability is estimated from the student's latest assessment attempt
            max_attempt = max(r["attempt_number"] for r in s_recs)
            latest_recs = [r for r in s_recs if r["attempt_number"] == max_attempt]

            est = estimate_theta_mle(latest_recs, diff_map, min_items=1)
            student_estimates[usn] = est

            theta_record = existing_thetas.get(usn)
            if not theta_record:
                theta_record = StudentTheta(usn=usn)
                db.session.add(theta_record)
                existing_thetas[usn] = theta_record

            theta_record.theta = est["theta"]
            theta_record.std_error = est["std_error"]
            theta_record.responses_count = est["responses_count"]
            theta_record.correct_count = est["correct_count"]
            theta_record.estimation_status = est["status"]
            theta_record.quizzes_attempted = max_attempt
            theta_record.metadata_json = json.dumps({
                "latest_attempt": max_attempt,
                "total_responses": len(s_recs),
                "timestamp": datetime.utcnow().isoformat(),
            })
            theta_record.updated_at = datetime.utcnow()

        db.session.commit()

        return {
            "status": "SUCCESS",
            "items_calibrated": len(calibrations),
            "students_estimated": len(student_estimates),
            "calibrated_items": {
                qid: {
                    "b": c["difficulty_b"],
                    "status": c["calibration_status"],
                    "p_val": c["proportion_correct"],
                }
                for qid, c in calibrations.items()
            },
        }

    # Backward compatibility method for legacy web quizzes
    @staticmethod
    def update_student_theta(attempt_id: int):
        attempt = Attempt.query.get(attempt_id)
        if not attempt:
            raise ValueError("Attempt not found")
        usn = attempt.usn

        theta_record = StudentTheta.query.filter_by(usn=usn).first()
        if theta_record is None:
            theta_record = StudentTheta(usn=usn, theta=0.0, quizzes_attempted=0)
            db.session.add(theta_record)
            db.session.commit()

        theta = theta_record.theta
        responses = attempt.responses

        for resp in responses:
            question = Question.query.get(resp.question_id)
            diff = question.difficulty_value if question else 0.0
            theta_before = theta
            theta = update_theta(theta=theta, difficulty=diff, is_correct=resp.is_correct)
            history = ThetaHistory(
                usn=usn,
                attempt_id=attempt.id,
                question_id=resp.question_id,
                theta_before=theta_before,
                theta_after=theta,
            )
            db.session.add(history)

        theta_record.theta = theta
        theta_record.quizzes_attempted += 1
        db.session.commit()
        return theta