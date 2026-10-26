import csv
import io
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, abort, Response as FlaskResponse
)
from .models import ThetaHistory, db, Teacher, Quiz, Question, Attempt, Response
from .auth import teacher_required
from .routes_student import AVAILABLE_COURSES
from app.models import StudentTheta


teacher_bp = Blueprint("teacher", __name__)


@teacher_bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        teacher_id = request.form.get("teacher_id", "").strip().upper()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        password = request.form.get("password", "")

        if not teacher_id or not first_name or not last_name or not password:
            flash("All fields are required.", "danger")
            return render_template("teacher/register.html")

        if Teacher.query.get(teacher_id):
            flash("A teacher with this Teacher ID is already registered.", "danger")
            return render_template("teacher/register.html")

        teacher = Teacher(teacher_id=teacher_id, first_name=first_name, last_name=last_name)
        teacher.set_password(password)
        db.session.add(teacher)
        db.session.commit()

        flash("Registration successful. Please log in.", "success")
        return redirect(url_for("teacher.login"))

    return render_template("teacher/register.html")


@teacher_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        teacher_id = request.form.get("teacher_id", "").strip().upper()
        password = request.form.get("password", "")

        teacher = Teacher.query.get(teacher_id)
        if not teacher or not teacher.check_password(password):
            flash("Invalid Teacher ID or password.", "danger")
            return render_template("teacher/login.html")

        session.clear()
        session["role"] = "teacher"
        session["user_id"] = teacher.teacher_id
        flash(f"Welcome, {teacher.first_name}!", "success")
        return redirect(url_for("teacher.dashboard"))

    return render_template("teacher/login.html")


@teacher_bp.route("/dashboard")
@teacher_required
def dashboard():
    teacher = Teacher.query.get_or_404(session["user_id"])
    quizzes = Quiz.query.filter_by(teacher_id=teacher.teacher_id).order_by(Quiz.created_at.desc()).all()
    return render_template("teacher/dashboard.html", teacher=teacher, quizzes=quizzes)


@teacher_bp.route("/quiz/create", methods=["GET", "POST"])
@teacher_required
def create_quiz():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        course = request.form.get("course", AVAILABLE_COURSES[0])

        texts = request.form.getlist("question_text[]")
        opt_a = request.form.getlist("option_a[]")
        opt_b = request.form.getlist("option_b[]")
        opt_c = request.form.getlist("option_c[]")
        opt_d = request.form.getlist("option_d[]")
        correct = request.form.getlist("correct_option[]")
        topics = request.form.getlist("topic[]")
        difficulties = request.form.getlist("difficulty[]")
        marks_list = request.form.getlist("marks[]")

        if not title:
            flash("Quiz title is required.", "danger")
            return render_template("teacher/create_quiz.html", courses=AVAILABLE_COURSES)

        if len(texts) == 0:
            flash("Add at least one question.", "danger")
            return render_template("teacher/create_quiz.html", courses=AVAILABLE_COURSES)

        quiz = Quiz(
            title=title,
            course=course if course in AVAILABLE_COURSES else AVAILABLE_COURSES[0],
            teacher_id=session["user_id"],
            is_published=False,
        )
        db.session.add(quiz)
        db.session.flush()

        for i in range(len(texts)):
            if not texts[i].strip():
                continue
            try:
                marks_val = float(marks_list[i]) if marks_list[i] else 1.0
            except (ValueError, IndexError):
                marks_val = 1.0

            db.session.add(Question(
                quiz_id=quiz.id,
                order_index=i,
                question_text=texts[i].strip(),
                option_a=opt_a[i].strip(),
                option_b=opt_b[i].strip(),
                option_c=opt_c[i].strip(),
                option_d=opt_d[i].strip(),
                correct_option=correct[i].strip().upper(),
                concept_tag=topics[i].strip(),
                topic=topics[i].strip(),
                difficulty=(difficulties[i].strip() or None) if i < len(difficulties) else None,
                marks=marks_val,
            ))

        db.session.commit()
        flash("Quiz created as a draft. Review it and publish when ready.", "success")
        return redirect(url_for("teacher.quiz_detail", quiz_id=quiz.id))

    return render_template("teacher/create_quiz.html", courses=AVAILABLE_COURSES)


@teacher_bp.route("/quiz/<int:quiz_id>")
@teacher_required
def quiz_detail(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.teacher_id != session["user_id"]:
        abort(403)
    attempt_count = Attempt.query.filter_by(quiz_id=quiz.id).count()
    return render_template("teacher/quiz_detail.html", quiz=quiz, attempt_count=attempt_count)


@teacher_bp.route("/quiz/<int:quiz_id>/publish", methods=["POST"])
@teacher_required
def publish_quiz(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.teacher_id != session["user_id"]:
        abort(403)
    if not quiz.questions:
        flash("Cannot publish a quiz with no questions.", "danger")
        return redirect(url_for("teacher.quiz_detail", quiz_id=quiz.id))

    quiz.is_published = True
    quiz.published_at = datetime.utcnow()
    db.session.commit()
    flash("Quiz published. All students in this course now see it.", "success")
    return redirect(url_for("teacher.quiz_detail", quiz_id=quiz.id))


@teacher_bp.route("/quiz/<int:quiz_id>/dashboard")
@teacher_required
def quiz_dashboard(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.teacher_id != session["user_id"]:
        abort(403)

    attempts = (
        Attempt.query.filter_by(quiz_id=quiz.id)
        .order_by(Attempt.percentage.desc())
        .all()
    )


    # -----------------------------
    # IRT Analytics
    # -----------------------------

    theta_map = {}

    theta_values = []

    for attempt in attempts:

        theta_record = StudentTheta.query.filter_by(
            usn=attempt.usn
        ).first()

        theta = round(theta_record.theta, 2) if theta_record else 0.0

        theta_map[attempt.usn] = theta

        theta_values.append(theta)

    average_theta = (
        round(sum(theta_values) / len(theta_values), 2)
        if theta_values else 0.0
    )

    summary = {
        "total_attempts": len(attempts),
        "average_percentage": round(sum(a.percentage for a in attempts) / len(attempts), 2) if attempts else 0,
        "highest_percentage": max((a.percentage for a in attempts), default=0),
        "lowest_percentage": min((a.percentage for a in attempts), default=0),
    }

    # Per-question accuracy (drives the "which questions tripped everyone up" chart)
    question_stats = []
    for q in quiz.questions:
        responses = Response.query.filter_by(question_id=q.id).all()
        total = len(responses)
        correct = sum(1 for r in responses if r.is_correct)
        question_stats.append({
            "label": f"Q{q.order_index + 1}",
            "text": q.question_text,
            "topic": q.topic,
            "total": total,
            "correct": correct,
            "accuracy": round((correct / total) * 100, 2) if total else 0,
        })

    # Per-topic accuracy (aggregates all questions sharing a topic)
    topic_map = {}
    for q in quiz.questions:
        responses = Response.query.filter_by(question_id=q.id).all()
        bucket = topic_map.setdefault(q.topic, {"correct": 0, "total": 0})
        bucket["total"] += len(responses)
        bucket["correct"] += sum(1 for r in responses if r.is_correct)

    topic_stats = [
        {
            "topic": topic,
            "accuracy": round((v["correct"] / v["total"]) * 100, 2) if v["total"] else 0,
            "total": v["total"],
            "correct": v["correct"],
        }
        for topic, v in topic_map.items()
    ]

    # Score distribution buckets for the histogram
    buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for a in attempts:
        p = a.percentage
        if p <= 20:
            buckets["0-20"] += 1
        elif p <= 40:
            buckets["21-40"] += 1
        elif p <= 60:
            buckets["41-60"] += 1
        elif p <= 80:
            buckets["61-80"] += 1
        else:
            buckets["81-100"] += 1

    return render_template(
        "teacher/quiz_dashboard.html",
        quiz=quiz,
        attempts=attempts,
        summary=summary,
        question_stats=question_stats,
        topic_stats=topic_stats,
        buckets=buckets,
        theta_map=theta_map,
        average_theta=average_theta,
    )
    


@teacher_bp.route("/attempt/<int:attempt_id>")
@teacher_required
def view_attempt(attempt_id):
    attempt = Attempt.query.get_or_404(attempt_id)
    if attempt.quiz.teacher_id != session["user_id"]:
        abort(403)

    rows = []
    for response in sorted(attempt.responses, key=lambda r: r.question.order_index):
        rows.append({
            "question": response.question,
            "selected": response.selected_option,
            "is_correct": response.is_correct,
        })

    return render_template("teacher/attempt_detail.html", attempt=attempt, rows=rows)


@teacher_bp.route("/quiz/<int:quiz_id>/report.csv")
@teacher_required
def download_report(quiz_id):
    quiz = Quiz.query.get_or_404(quiz_id)
    if quiz.teacher_id != session["user_id"]:
        abort(403)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
         "USN",
        "Student Name",
        "Theta_Before",
        "Theta_After",
        "Quiz ID",
        "Question ID",
        "Selected Option",
        "Correct Option",
        "Correct/Incorrect",
        "Topic",
        "Marks",
        "Final Score",
        "Timestamp",
    ])

    attempts = (
        Attempt.query.filter_by(quiz_id=quiz.id)
        .order_by(Attempt.usn)
        .all()
    )
    for attempt in attempts:
        student = attempt.student
        theta_record = StudentTheta.query.filter_by(
            usn=student.usn
        ).first()

        theta = round(theta_record.theta, 2) if theta_record else 0.0

        for resp in sorted(attempt.responses, key=lambda r: r.question_id):
            question = resp.question
            history = ThetaHistory.query.filter_by(
                    attempt_id=attempt.id,
                    question_id=question.id
            ).first()

            theta_before = round(history.theta_before, 4) if history else 0.0
            theta_after = round(history.theta_after, 4) if history else 0.0
            writer.writerow([
                student.usn,
                student.full_name,
                theta_before,
                theta_after,
                quiz.id,
                question.id,
                resp.selected_option or "",
                question.correct_option,
                "Correct" if resp.is_correct else "Incorrect",
                resp.topic,
                resp.marks_awarded,
                attempt.score,
                resp.timestamp.strftime("%Y-%m-%d %H:%M:%S") if resp.timestamp else "",
            ])

    output = buffer.getvalue()
    filename = f"quiz_{quiz.id}_report.csv"
    return FlaskResponse(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@teacher_bp.route("/import-csv", methods=["POST"])
@teacher_required
def import_csv():
    """
    Teacher endpoint to upload and ingest a weekly assessment CSV,
    followed by automated IRT and BKT recalculation.
    """
    from app.pipeline import AdaptivePipeline
    from flask import jsonify

    uploaded_file = request.files.get("file")
    file_path = request.form.get("file_path") or (request.get_json(silent=True) or {}).get("file_path")

    if uploaded_file and uploaded_file.filename:
        try:
            res = AdaptivePipeline.run_pipeline(uploaded_file, filename=uploaded_file.filename)
            return jsonify(res), 200
        except Exception as e:
            return jsonify({"status": "ERROR", "message": str(e)}), 400

    elif file_path:
        try:
            res = AdaptivePipeline.run_pipeline(file_path)
            return jsonify(res), 200
        except Exception as e:
            return jsonify({"status": "ERROR", "message": str(e)}), 400

    return jsonify({"status": "ERROR", "message": "No file uploaded and no file_path provided."}), 400


@teacher_bp.route("/recalculate-models", methods=["POST"])
@teacher_required
def recalculate_models():
    """
    Teacher endpoint to recalculate IRT and BKT models on existing persistent responses.
    """
    from app.pipeline import AdaptivePipeline
    from flask import jsonify

    try:
        res = AdaptivePipeline.recalculate_models_only()
        return jsonify(res), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 500


@teacher_bp.route("/analytics/summary", methods=["GET"])
@teacher_required
def analytics_summary():
    """
    Teacher endpoint to view database summary of batches, calibrations, thetas, and masteries.
    """
    from flask import jsonify
    from app.models import ImportBatch, ItemCalibration, StudentTheta, StudentMastery, ImportedResponse

    batches = ImportBatch.query.order_by(ImportBatch.created_at.desc()).all()
    items = ItemCalibration.query.order_by(ItemCalibration.difficulty_b.asc()).all()
    thetas = StudentTheta.query.order_by(StudentTheta.theta.desc()).limit(50).all()
    masteries = StudentMastery.query.order_by(StudentMastery.mastery.desc()).limit(50).all()

    return jsonify({
        "total_batches": len(batches),
        "total_imported_responses": ImportedResponse.query.count(),
        "batches": [
            {
                "id": b.id,
                "filename": b.filename,
                "status": b.status,
                "total_rows": b.total_rows,
                "valid_rows": b.valid_rows,
                "error_summary": b.error_summary,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }
            for b in batches
        ],
        "items": [
            {
                "question_id": i.question_id,
                "concept_tag": i.concept_tag,
                "bloom_level": i.bloom_level,
                "difficulty_b": i.difficulty_b,
                "p_val": i.proportion_correct,
                "status": i.calibration_status,
            }
            for i in items
        ],
        "thetas_sample": [
            {"usn": t.usn, "theta": t.theta, "status": t.estimation_status}
            for t in thetas
        ],
        "masteries_sample": [
            {"usn": m.usn, "concept": m.concept_tag, "mastery": m.mastery}
            for m in masteries
        ],
    }), 200



@teacher_bp.route("/analytics/students")
@teacher_required
def students_analytics():
    """
    Teacher view: all imported students ranked by IRT theta,
    with BKT mastery per concept displayed alongside.
    """
    from app.models import StudentTheta, StudentMastery, Student
    from collections import defaultdict

    # All theta records ordered by theta desc
    thetas = StudentTheta.query.order_by(StudentTheta.theta.desc()).all()

    # Build student name lookup (some imported-only students have placeholder names)
    student_lookup = {s.usn: s for s in Student.query.all()}

    # All mastery records, grouped by usn
    mastery_by_usn = defaultdict(dict)
    for m in StudentMastery.query.all():
        mastery_by_usn[m.usn][m.concept_tag] = m

    # Sorted unique concept list
    concepts = sorted({
        m.concept_tag
        for records in mastery_by_usn.values()
        for m in records.values()
    })

    # Build rows for template
    rows = []
    for t in thetas:
        student = student_lookup.get(t.usn)
        name = student.full_name if student and student.first_name != t.usn else t.usn
        rows.append({
            "usn": t.usn,
            "name": name,
            "theta": round(t.theta, 3),
            "std_error": round(t.std_error, 3) if t.std_error else None,
            "estimation_status": t.estimation_status,
            "responses_count": t.responses_count,
            "correct_count": t.correct_count,
            "updated_at": t.updated_at,
            "mastery": {
                c: round(mastery_by_usn[t.usn][c].mastery, 4)
                if c in mastery_by_usn[t.usn] else None
                for c in concepts
            },
        })

    # Theta distribution buckets for histogram
    buckets = {"-4 to -3": 0, "-3 to -2": 0, "-2 to -1": 0,
                "-1 to 0": 0, "0 to 1": 0, "1 to 2": 0,
                "2 to 3": 0, "3 to 4": 0}
    for r in rows:
        th = r["theta"]
        if th < -3:   buckets["-4 to -3"] += 1
        elif th < -2: buckets["-3 to -2"] += 1
        elif th < -1: buckets["-2 to -1"] += 1
        elif th < 0:  buckets["-1 to 0"] += 1
        elif th < 1:  buckets["0 to 1"] += 1
        elif th < 2:  buckets["1 to 2"] += 1
        elif th < 3:  buckets["2 to 3"] += 1
        else:         buckets["3 to 4"] += 1

    avg_theta = round(sum(r["theta"] for r in rows) / len(rows), 3) if rows else 0

    return render_template(
        "teacher/students_analytics.html",
        rows=rows,
        concepts=concepts,
        buckets=buckets,
        avg_theta=avg_theta,
        total_students=len(rows),
    )


@teacher_bp.route("/student/<usn>")
@teacher_required
def student_profile(usn):
    """
    Teacher view: IRT theta + BKT concept mastery detail for one student.
    """
    from app.models import StudentTheta, StudentMastery, Student, ImportedResponse

    theta_record = StudentTheta.query.filter_by(usn=usn).first_or_404()
    student = Student.query.get(usn)
    name = student.full_name if student and student.first_name != usn else usn

    masteries = (
        StudentMastery.query.filter_by(usn=usn)
        .order_by(StudentMastery.mastery.desc())
        .all()
    )

    # Response summary from imported records
    from sqlalchemy import func
    total_imported = ImportedResponse.query.filter_by(student_id=usn).count()
    total_correct = ImportedResponse.query.filter_by(student_id=usn, is_correct=True).count()
    total_unanswered = ImportedResponse.query.filter_by(student_id=usn, is_unanswered=True).count()

    return render_template(
        "teacher/student_profile.html",
        usn=usn,
        name=name,
        theta_record=theta_record,
        masteries=masteries,
        total_imported=total_imported,
        total_correct=total_correct,
        total_unanswered=total_unanswered,
    )
