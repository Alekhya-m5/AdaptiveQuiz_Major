import math

from app.models import (
    db,
    Attempt,
    Response,
    Question,
    StudentTheta,
    StudentMastery,
)


# BKT parameters
P_LEARN = 0.15
P_GUESS = 0.20
P_SLIP = 0.10


class BKTService:

    @staticmethod
    def theta_to_mastery(theta):
        """
        Convert IRT theta into initial BKT mastery.
        """

        return 1 / (1 + math.exp(-theta))

    @staticmethod
    def update_mastery(
        mastery,
        is_correct,
        p_learn=P_LEARN,
        p_guess=P_GUESS,
        p_slip=P_SLIP
    ):
        """
        Update mastery after one response.
        """

        # Probability of observing a correct answer
        p_correct = (
            mastery * (1 - p_slip)
            + (1 - mastery) * p_guess
        )

        if is_correct:

            # Probability student knew the concept
            posterior = (
                mastery * (1 - p_slip)
            ) / p_correct

        else:

            # Probability of observing an incorrect answer
            p_incorrect = (
                mastery * p_slip
                + (1 - mastery) * (1 - p_guess)
            )

            posterior = (
                mastery * p_slip
            ) / p_incorrect

        # Learning transition
        new_mastery = (
            posterior
            + (1 - posterior) * p_learn
        )

        return max(0.0, min(1.0, new_mastery))

    @staticmethod
    def update_student_mastery(attempt_id):

        print(
            f"Updating BKT mastery for attempt_id: {attempt_id}"
        )

        # --------------------------------
        # Get attempt
        # --------------------------------

        attempt = Attempt.query.get(attempt_id)

        if not attempt:
            raise ValueError("Attempt not found")

        usn = attempt.usn

        # --------------------------------
        # Get IRT theta
        # --------------------------------

        theta_record = StudentTheta.query.filter_by(
            usn=usn
        ).first()

        theta = (
            theta_record.theta
            if theta_record
            else 0.0
        )

        print(f"IRT theta = {theta}")

        # --------------------------------
        # Get responses
        # --------------------------------

        responses = (
            Response.query
            .filter_by(attempt_id=attempt_id)
            .order_by(Response.id)
            .all()
        )

        # --------------------------------
        # Process each response
        # --------------------------------

        for response in responses:

            question = Question.query.get(
                response.question_id
            )

            if not question:
                continue

            concept = question.concept_tag

            if not concept:
                continue

            # --------------------------------
            # Find student's mastery
            # --------------------------------

            mastery_record = StudentMastery.query.filter_by(
                usn=usn,
                concept_tag=concept
            ).first()

            # --------------------------------
            # First time seeing concept
            # --------------------------------

            if mastery_record is None:

                initial_mastery = (
                    BKTService.theta_to_mastery(theta)
                )

                mastery_record = StudentMastery(
                    usn=usn,
                    concept_tag=concept,
                    mastery=initial_mastery,
                    attempts=0
                )

                db.session.add(mastery_record)

                print(
                    f"New concept: {concept}"
                )

            # --------------------------------
            # Current mastery
            # --------------------------------

            old_mastery = mastery_record.mastery

            # --------------------------------
            # BKT update
            # --------------------------------

            new_mastery = BKTService.update_mastery(
                mastery=old_mastery,
                is_correct=response.is_correct
            )

            mastery_record.mastery = new_mastery
            mastery_record.attempts += 1

            print(
                f"Concept: {concept} | "
                f"Correct: {response.is_correct} | "
                f"Mastery: {old_mastery:.4f} -> "
                f"{new_mastery:.4f}"
            )

        db.session.commit()

        return True