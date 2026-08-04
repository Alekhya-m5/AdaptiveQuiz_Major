"""
IRT Service

Connects the Rasch model with the database.
"""

from app.models import (
    db,
    Attempt,
    Response,
    Question,
    StudentTheta,
    ThetaHistory
)


from app.irt.theta import update_theta


class IRTService:

    @staticmethod
    def update_student_theta(attempt_id: int):
        print(f"Updating student theta for attempt_id: {attempt_id}")

        # -----------------------------
        # Get Attempt
        # -----------------------------
        attempt = Attempt.query.get(attempt_id)

        if not attempt:
            raise ValueError("Attempt not found")

        usn = attempt.usn

        # -----------------------------
        # Get Student Theta
        # -----------------------------
        theta_record = StudentTheta.query.filter_by(usn=usn).first()

        if theta_record is None:

            theta_record = StudentTheta(
                usn=usn,
                theta=0.0,
                quizzes_attempted=0
            )

            db.session.add(theta_record)
            db.session.commit()

        theta = theta_record.theta

        # -----------------------------
        # Get All Responses
        # -----------------------------
        responses = Response.query.filter_by(
            attempt_id=attempt_id
        ).all()

        # -----------------------------
        # Update theta question-by-question
        # -----------------------------
        for response in responses:

            question = Question.query.get(response.question_id)

            theta_before = theta

            theta = update_theta(
                theta=theta,
                difficulty=question.difficulty_value,
                is_correct=response.is_correct
            )

            history = ThetaHistory(
                usn=usn,
                attempt_id=attempt.id,
                question_id=response.question_id,
                theta_before=theta_before,
                theta_after=theta
            )

            db.session.add(history)

        # -----------------------------
        # Save Updated Theta
        # -----------------------------
        theta_record.theta = theta
        theta_record.quizzes_attempted += 1

        db.session.commit()

        return theta