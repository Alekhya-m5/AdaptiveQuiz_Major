"""
Bayesian Knowledge Tracing (BKT) Service.

Implements standard Corbett & Anderson (1995) BKT for concept-level mastery estimation.
Processes responses in chronological order per (student, concept_tag) pair.
Rerunning is idempotent and prevents double-counting.
"""

from collections import defaultdict
from datetime import datetime
import json
import logging
import math
from typing import Dict, List, Any, Optional

from app.models import (
    db,
    ImportedResponse,
    StudentMastery,
    StudentTheta,
    Student,
    Attempt,
    Question,
    Response,
)

logger = logging.getLogger(__name__)

# Default BKT parameters (Corbett & Anderson standard parameters)
DEFAULT_P_L0 = 0.20     # Initial knowledge probability
DEFAULT_P_TRANS = 0.15  # Learning transition probability P(T)
DEFAULT_P_GUESS = 0.20  # Guess probability P(G)
DEFAULT_P_SLIP = 0.10   # Slip probability P(S)


class BKTService:
    """
    Production BKT Service for Concept Mastery Estimation.
    """

    @staticmethod
    def update_mastery(
        mastery: float,
        is_correct: bool,
        p_learn: float = DEFAULT_P_TRANS,
        p_guess: float = DEFAULT_P_GUESS,
        p_slip: float = DEFAULT_P_SLIP,
    ) -> float:
        """
        Compute standard BKT posterior update and learning transition for a single observation.

        Equations:
        P(obs=correct) = L * (1 - S) + (1 - L) * G
        P(L | correct) = (L * (1 - S)) / P(obs=correct)

        P(obs=incorrect) = L * S + (1 - L) * (1 - G)
        P(L | incorrect) = (L * S) / P(obs=incorrect)

        P(L_next) = P(L | obs) + (1 - P(L | obs)) * P(T)
        """
        # Posterior update
        if is_correct:
            p_obs = mastery * (1.0 - p_slip) + (1.0 - mastery) * p_guess
            posterior = (mastery * (1.0 - p_slip)) / max(p_obs, 1e-12)
        else:
            p_obs = mastery * p_slip + (1.0 - mastery) * (1.0 - p_guess)
            posterior = (mastery * p_slip) / max(p_obs, 1e-12)

        # Transition update
        new_mastery = posterior + (1.0 - posterior) * p_learn
        return max(0.0, min(1.0, new_mastery))

    @classmethod
    def recalculate_all(
        cls,
        p_l0: float = DEFAULT_P_L0,
        p_learn: float = DEFAULT_P_TRANS,
        p_guess: float = DEFAULT_P_GUESS,
        p_slip: float = DEFAULT_P_SLIP,
        unanswered_policy: str = "skip",
        use_irt_prior: bool = False,
    ) -> Dict[str, Any]:
        """
        Recalculate BKT concept mastery for all students and concepts
        using only verified external imported assessment responses.

        Parameters
        ----------
        p_l0 : float
            Default initial mastery probability P(L0).
        p_learn : float
            Transition probability P(T).
        p_guess : float
            Guess probability P(G).
        p_slip : float
            Slip probability P(S).
        unanswered_policy : str, default 'skip'
            'skip' (unanswered questions provide no skill evidence, mastery unchanged)
            or 'incorrect' (unanswered questions treated as incorrect evidence).
        use_irt_prior : bool, default False
            If True, initialize P(L0) = sigmoid(theta) from StudentTheta.
            If False (standard BKT), use fixed p_l0.

        Returns
        -------
        Dict[str, Any]
            Recalculation summary.
        """
        # Fetch verified imported assessment responses ordered chronologically
        responses = (
            ImportedResponse.query.filter_by(source="imported_csv")
            .order_by(
                ImportedResponse.submitted_at.asc(),
                ImportedResponse.attempt_number.asc(),
                ImportedResponse.question_no.asc(),
            )
            .all()
        )

        if not responses:
            return {
                "status": "NO_DATA",
                "message": "No imported assessment records found for BKT calculation.",
                "student_concepts_updated": 0,
            }

        # Lookup theta if use_irt_prior is enabled
        theta_lookup = {}
        if use_irt_prior:
            for st in StudentTheta.query.all():
                theta_lookup[st.usn] = st.theta

        # Group responses by (student_id, concept_tag)
        grouped = defaultdict(list)
        for r in responses:
            if not r.concept_tag or not r.concept_tag.strip():
                continue
            grouped[(r.student_id, r.concept_tag.strip())].append(r)

        updated_count = 0
        student_cache = {s.usn: s for s in Student.query.all()}
        existing_masteries = {
            (m.usn, m.concept_tag): m
            for m in StudentMastery.query.all()
        }

        for (usn, concept), rec_list in grouped.items():
            # Ensure student exists in students table
            if usn not in student_cache:
                student = Student(usn=usn, first_name=usn, last_name="", course="RM", password_hash="placeholder")
                db.session.add(student)
                student_cache[usn] = student

            # Initial mastery P(L0)
            if use_irt_prior and usn in theta_lookup:
                th = theta_lookup[usn]
                current_mastery = 1.0 / (1.0 + math.exp(-max(-35.0, min(35.0, th))))
            else:
                current_mastery = p_l0

            obs_count = 0
            correct_count = 0
            unanswered_count = 0

            # Step through each observation in chronological order
            for r in rec_list:
                if r.is_unanswered:
                    unanswered_count += 1
                    if unanswered_policy == "skip":
                        # No evidence update
                        continue
                    else:
                        is_corr = False
                else:
                    is_corr = r.is_correct

                if is_corr:
                    correct_count += 1
                obs_count += 1

                current_mastery = cls.update_mastery(
                    mastery=current_mastery,
                    is_correct=is_corr,
                    p_learn=p_learn,
                    p_guess=p_guess,
                    p_slip=p_slip,
                )

            # Persist to student_mastery
            mastery_rec = existing_masteries.get((usn, concept))
            if not mastery_rec:
                mastery_rec = StudentMastery(
                    usn=usn,
                    concept_tag=concept,
                )
                db.session.add(mastery_rec)
                existing_masteries[(usn, concept)] = mastery_rec

            mastery_rec.mastery = round(current_mastery, 4)
            mastery_rec.attempts = obs_count + (unanswered_count if unanswered_policy == "skip" else 0)
            mastery_rec.correct_count = correct_count
            mastery_rec.unanswered_count = unanswered_count
            mastery_rec.last_p_slip = p_slip
            mastery_rec.last_p_guess = p_guess
            mastery_rec.last_p_learn = p_learn
            mastery_rec.metadata_json = json.dumps({
                "unanswered_policy": unanswered_policy,
                "use_irt_prior": use_irt_prior,
                "p_l0": p_l0,
                "total_observations": obs_count,
                "timestamp": datetime.utcnow().isoformat(),
            })
            mastery_rec.last_updated = datetime.utcnow()
            updated_count += 1

        db.session.commit()

        return {
            "status": "SUCCESS",
            "student_concepts_updated": updated_count,
            "unique_students": len({u for u, _ in grouped.keys()}),
            "unique_concepts": len({c for _, c in grouped.keys()}),
            "parameters": {
                "p_l0": p_l0,
                "p_learn": p_learn,
                "p_guess": p_guess,
                "p_slip": p_slip,
                "unanswered_policy": unanswered_policy,
                "use_irt_prior": use_irt_prior,
            },
        }

    # Backward compatibility method for legacy web quizzes
    @staticmethod
    def theta_to_mastery(theta):
        return 1.0 / (1.0 + math.exp(-theta))

    @staticmethod
    def update_student_mastery(attempt_id):
        attempt = Attempt.query.get(attempt_id)
        if not attempt:
            raise ValueError("Attempt not found")
        usn = attempt.usn

        theta_record = StudentTheta.query.filter_by(usn=usn).first()
        theta = theta_record.theta if theta_record else 0.0

        responses = (
            Response.query.filter_by(attempt_id=attempt_id)
            .order_by(Response.id)
            .all()
        )

        for resp in responses:
            question = Question.query.get(resp.question_id)
            if not question or not question.concept_tag:
                continue

            concept = question.concept_tag
            mastery_record = StudentMastery.query.filter_by(
                usn=usn, concept_tag=concept
            ).first()

            if mastery_record is None:
                initial_mastery = BKTService.theta_to_mastery(theta)
                mastery_record = StudentMastery(
                    usn=usn,
                    concept_tag=concept,
                    mastery=initial_mastery,
                    attempts=0,
                )
                db.session.add(mastery_record)

            old_mastery = mastery_record.mastery
            new_mastery = BKTService.update_mastery(
                mastery=old_mastery, is_correct=resp.is_correct
            )
            mastery_record.mastery = new_mastery
            mastery_record.attempts += 1

        db.session.commit()
        return True