from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, abort
from .models import StudentTheta, db, Student, Quiz, Question, Attempt, Response
from .auth import student_required
from app.irt.service import IRTService
from app.models import StudentTheta
from app.bkt.services import BKTService

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
