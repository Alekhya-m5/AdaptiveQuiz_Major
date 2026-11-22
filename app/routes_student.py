from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from .models import (
    StudentTheta, db, Student, Quiz, Question, Attempt, Response,
    StudentMastery, DocumentChunk, GeneratedQuestionRecord
)
from .auth import student_required
from app.irt.service import IRTService
from app.irt.theta import update_theta
from app.bkt.services import BKTService
from app.ai.generator import generate_adaptive_question

student_bp = Blueprint("student", __name__)

# Fixed list of available courses. "RM" (Research Methodology) is the only
# course for now; add more strings here when the syllabus expands.
AVAILABLE_COURSES = ["RM"]


@student_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        usn = request.form.get("usn", "").strip().upper()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        password = request.form.get("password", "")
        course = request.form.get("course", AVAILABLE_COURSES[0])

        if not usn or not first_name or not last_name or not password:
            flash("All fields are required.", "danger")
            return render_template("student/register.html", courses=AVAILABLE_COURSES)

        if Student.query.get(usn):
            flash("A student with this USN is already registered.", "danger")
            return render_template("student/register.html", courses=AVAILABLE_COURSES)

        student = Student(
            usn=usn, first_name=first_name, last_name=last_name,
            course=course if course in AVAILABLE_COURSES else AVAILABLE_COURSES[0],
        )
        student.set_password(password)
        db.session.add(student)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("student.login"))

    return render_template("student/register.html", courses=AVAILABLE_COURSES)


@student_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usn = request.form.get("usn", "").strip().upper()
        password = request.form.get("password", "")

        student = Student.query.get(usn)
        if not student or not student.check_password(password):
            flash("Invalid USN or password.", "danger")
            return render_template("student/login.html")

        session.clear()
        session["role"] = "student"
        session["user_id"] = student.usn
        flash(f"Welcome, {student.first_name}!", "success")
        return redirect(url_for("student.dashboard"))

    return render_template("student/login.html")


@student_bp.route("/dashboard")
@student_required
def dashboard():
    student = Student.query.get_or_404(session["user_id"])
    quizzes = Quiz.query.filter_by(course=student.course, is_published=True).order_by(Quiz.created_at).all()

    attempted_quiz_ids = {
        a.quiz_id for a in Attempt.query.filter_by(usn=student.usn).all()
    }

    return render_template(
        "student/dashboard.html",
        student=student,
        quizzes=quizzes,
        attempted_quiz_ids=attempted_quiz_ids,
    )


@student_bp.route("/quiz/<int:quiz_id>/take", methods=["GET", "POST"])
@student_required
def take_quiz(quiz_id):
    student = Student.query.get_or_404(session["user_id"])
    quiz = Quiz.query.get_or_404(quiz_id)

    if not quiz.is_published or quiz.course != student.course:
        abort(404)

    existing = Attempt.query.filter_by(quiz_id=quiz.id, usn=student.usn).first()
    if existing:
        flash("You have already attempted this quiz.", "info")
        return redirect(url_for("student.result_detail", attempt_id=existing.id))

    if request.method == "POST":
        attempt = Attempt(
            quiz_id=quiz.id, usn=student.usn,
            started_at=datetime.utcnow(), submitted_at=datetime.utcnow(),
        )
        db.session.add(attempt)
        db.session.flush()  # get attempt.id before commit

        score = 0.0
        total_marks = 0.0
        correct_count = 0
        wrong_count = 0

        for question in quiz.questions:
            selected = request.form.get(f"question_{question.id}")
            is_correct = bool(selected) and selected == question.correct_option
            marks_awarded = question.marks if is_correct else 0.0

            total_marks += question.marks
            score += marks_awarded
            if selected is None:
                wrong_count += 1
            elif is_correct:
                correct_count += 1
            else:
                wrong_count += 1

            db.session.add(Response(
                attempt_id=attempt.id,
                question_id=question.id,
                selected_option=selected,
                is_correct=is_correct,
                marks_awarded=marks_awarded,
                concept_tag=question.concept_tag,
                topic=question.topic,
                timestamp=datetime.utcnow(),
            ))

        attempt.score = score
        attempt.total_marks = total_marks
        attempt.percentage = round((score / total_marks) * 100, 2) if total_marks else 0.0
        attempt.correct_count = correct_count
        attempt.wrong_count = wrong_count

        db.session.commit()
        flash("Quiz submitted successfully.", "success")
        IRTService.update_student_theta(attempt.id)
        BKTService.update_student_mastery(attempt.id)
        return redirect(url_for("student.result_detail", attempt_id=attempt.id))

    return render_template("student/take_quiz.html", quiz=quiz, student=student)


@student_bp.route("/analytics")
@student_required
def analytics():
    return redirect(url_for("student.results"))


@student_bp.route("/results")
@student_required
def results():
    from app.models import StudentMastery
    student = Student.query.get_or_404(session["user_id"])
    attempts = (
        Attempt.query.filter_by(usn=student.usn)
        .order_by(Attempt.submitted_at.desc())
        .all()
    )

    average_percentage = round(sum(a.percentage for a in attempts) / len(attempts), 2) if attempts else 0
    best_percentage = max((a.percentage for a in attempts), default=0)
    total_correct = sum(a.correct_count for a in attempts)
    total_wrong = sum(a.wrong_count for a in attempts)
    theta_record = StudentTheta.query.filter_by(usn=session["user_id"]).first()
    theta = theta_record.theta if theta_record else 0.0

    # Fetch BKT concept mastery for this student, ordered by mastery desc
    mastery_records = (
        StudentMastery.query
        .filter_by(usn=session["user_id"])
        .order_by(StudentMastery.mastery.desc())
        .all()
    )

    return render_template(
        "student/results.html",
        student=student,
        attempts=attempts,
        average_percentage=average_percentage,
        best_percentage=best_percentage,
        total_correct=total_correct,
        total_wrong=total_wrong,
        theta=theta,
        mastery_records=mastery_records,
    )


@student_bp.route("/results/<int:attempt_id>")
@student_required
def result_detail(attempt_id):
    student = Student.query.get_or_404(session["user_id"])
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.usn != student.usn:
        abort(403)

    rows = []
    for response in attempt.responses:
        rows.append({
            "question": response.question,
            "selected": response.selected_option,
            "is_correct": response.is_correct,
        })

    return render_template("student/result_detail.html", attempt=attempt, rows=rows)


# ---------------------------------------------------------------------------
# Adaptive RAG Quiz Generation & Practice
# ---------------------------------------------------------------------------

@student_bp.route("/adaptive-quiz", methods=["GET", "POST"])
@student_required
def adaptive_quiz():
    """Personalized adaptive practice quiz powered by RAG, IRT, BKT, and Qwen+LoRA."""
    student = Student.query.get_or_404(session["user_id"])
    theta_rec = StudentTheta.query.filter_by(usn=student.usn).first()
    current_theta = theta_rec.theta if theta_rec else 0.0

    # Fetch available concepts from documents and past masteries
    chunk_concepts = [
        c[0] for c in db.session.query(DocumentChunk.concept_tag)
        .filter(DocumentChunk.concept_tag.isnot(None))
        .distinct().all()
    ]
    mastery_concepts = [
        m.concept_tag for m in StudentMastery.query.filter_by(usn=student.usn).all()
    ]
    all_concepts = sorted(list(set(chunk_concepts + mastery_concepts)))

    # Identify lowest mastery concept for targeted recommendation
    lowest_mastery_rec = (
        StudentMastery.query.filter_by(usn=student.usn)
        .order_by(StudentMastery.mastery.asc())
        .first()
    )
    recommended_concept = lowest_mastery_rec.concept_tag if lowest_mastery_rec else (
        all_concepts[0] if all_concepts else "Research Approaches"
    )

    if request.method == "POST":
        selected_concept = request.form.get("concept", recommended_concept).strip()
        num_questions = min(3, max(1, int(request.form.get("num_questions", 1))))

        questions_list = []
        for _ in range(num_questions):
            q_res = generate_adaptive_question(
                course=student.course,
                concept_tag=selected_concept,
                usn=student.usn,
            )
            questions_list.append(q_res)

        return render_template(
            "student/take_adaptive_quiz.html",
            student=student,
            concept=selected_concept,
            current_theta=current_theta,
            questions=questions_list,
        )

    return render_template(
        "student/adaptive_quiz_setup.html",
        student=student,
        current_theta=current_theta,
        all_concepts=all_concepts,
        recommended_concept=recommended_concept,
    )


@student_bp.route("/adaptive-quiz/submit", methods=["POST"])
@student_required
def submit_adaptive_quiz():
    """Grade adaptive practice quiz and incrementally update student IRT theta and BKT mastery."""
    student = Student.query.get_or_404(session["user_id"])
    concept = request.form.get("concept", "Research Approaches").strip()

    record_ids = request.form.getlist("record_ids[]")
    results = []
    total_correct = 0

    theta_rec = StudentTheta.query.filter_by(usn=student.usn).first()
    if not theta_rec:
        theta_rec = StudentTheta(usn=student.usn, theta=0.0, quizzes_attempted=0)
        db.session.add(theta_rec)
        db.session.commit()

    mastery_rec = StudentMastery.query.filter_by(usn=student.usn, concept_tag=concept).first()
    if not mastery_rec:
        mastery_rec = StudentMastery(usn=student.usn, concept_tag=concept, mastery=0.20, attempts=0)
        db.session.add(mastery_rec)
        db.session.commit()

    theta_before = theta_rec.theta
    mastery_before = mastery_rec.mastery

    for r_id in record_ids:
        rec = GeneratedQuestionRecord.query.get(int(r_id))
        if not rec:
            continue

        user_choice = request.form.get(f"choice_{rec.id}", "").strip().upper()
        is_correct = (user_choice == rec.correct_option)
        if is_correct:
            total_correct += 1

        # Numerical difficulty
        diff_num = {"Easy": -1.0, "Medium": 0.0, "Hard": 1.0}.get(rec.target_difficulty, 0.0)

        # Update IRT theta
        theta_rec.theta = update_theta(theta_rec.theta, diff_num, is_correct)
        theta_rec.responses_count = (theta_rec.responses_count or 0) + 1
        if is_correct:
            theta_rec.correct_count = (theta_rec.correct_count or 0) + 1

        # Update BKT concept mastery
        mastery_rec.mastery = BKTService.update_mastery(mastery_rec.mastery, is_correct)
        mastery_rec.attempts = (mastery_rec.attempts or 0) + 1
        if is_correct:
            mastery_rec.correct_count = (mastery_rec.correct_count or 0) + 1

        results.append({
            "record": rec,
            "user_choice": user_choice,
            "is_correct": is_correct,
            "correct_option": rec.correct_option,
            "explanation": rec.explanation,
            "source_ref": rec.source_page_or_slide,
        })

    theta_rec.updated_at = datetime.utcnow()
    mastery_rec.last_updated = datetime.utcnow()
    db.session.commit()

    return render_template(
        "student/adaptive_quiz_result.html",
        student=student,
        concept=concept,
        results=results,
        total_questions=len(results),
        total_correct=total_correct,
        theta_before=round(theta_before, 3),
        theta_after=round(theta_rec.theta, 3),
        mastery_before=round(mastery_before, 3),
        mastery_after=round(mastery_rec.mastery, 3),
    )

