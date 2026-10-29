import csv
import io
import re
from collections import defaultdict
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    flash, session, abort, Response as FlaskResponse
)
from sqlalchemy import func
from .models import (
    ThetaHistory, db, Teacher, Quiz, Question, Attempt, Response,
    StudentTheta, StudentMastery, Student, ImportedResponse, ItemCalibration, ImportBatch
)
from .auth import teacher_required
from .routes_student import AVAILABLE_COURSES


def interpret_theta(theta):
    if theta is None:
        return "Not Estimated"
    if theta >= 2.0:
        return "Advanced (>= +2.0)"
    elif theta >= 1.0:
        return "Proficient (+1.0 to +2.0)"
    elif theta >= 0.0:
        return "Developing (0.0 to +1.0)"
    elif theta >= -1.0:
        return "Beginning (-1.0 to 0.0)"
    else:
        return "At Risk (< -1.0)"


def interpret_mastery(mastery):
    if mastery is None:
        return "No Data"
    if mastery >= 0.70:
        return "Mastered (>= 70%)"
    elif mastery >= 0.40:
        return "Developing (40-70%)"
    else:
        return "Needs Work (< 40%)"


def safe_filename(name):
    clean = re.sub(r"[^\w\-]+", "_", name).strip("_")
    return clean or "quiz_report"


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

    # Query curriculum assessment quizzes from imported_responses
    imported_quiz_rows = (
        db.session.query(
            ImportedResponse.quiz,
            ImportedResponse.subject,
            func.count(ImportedResponse.id).label("total_responses"),
            func.count(func.distinct(ImportedResponse.student_id)).label("total_students"),
            func.count(func.distinct(ImportedResponse.question_id)).label("total_questions"),
            func.min(ImportedResponse.submitted_at).label("first_submission"),
            func.max(ImportedResponse.submitted_at).label("last_submission"),
        )
        .group_by(ImportedResponse.quiz, ImportedResponse.subject)
        .order_by(func.min(ImportedResponse.submitted_at).asc())
        .all()
    )

    imported_quizzes = [
        {
            "title": row.quiz,
            "subject": row.subject or "RM",
            "total_responses": row.total_responses,
            "total_students": row.total_students,
            "total_questions": row.total_questions,
            "first_submission": row.first_submission,
            "last_submission": row.last_submission,
        }
        for row in imported_quiz_rows
    ]

    total_calibrated = ItemCalibration.query.count()
    total_students_theta = StudentTheta.query.count()
    total_mastery_records = StudentMastery.query.count()

    return render_template(
        "teacher/dashboard.html",
        teacher=teacher,
        quizzes=quizzes,
        imported_quizzes=imported_quizzes,
        total_calibrated=total_calibrated,
        total_students_theta=total_students_theta,
        total_mastery_records=total_mastery_records,
    )


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


@teacher_bp.route("/imported-quiz/analytics")
@teacher_required
def imported_quiz_analytics():
    """
    Teacher view: In-depth visual and tabular analytics for a selected imported assessment quiz.
    Displays student score distribution, attempt records, question IRT difficulties,
    and BKT concept mastery distributions.
    """
    quiz_title = request.args.get("quiz", "").strip()
    if not quiz_title:
        abort(404, description="Quiz parameter is required.")

    responses = (
        ImportedResponse.query.filter_by(quiz=quiz_title)
        .order_by(ImportedResponse.attempt_number.asc(), ImportedResponse.student_id.asc(), ImportedResponse.question_no.asc())
        .all()
    )
    if not responses:
        abort(404, description=f"Assessment quiz '{quiz_title}' not found.")

    subject = responses[0].subject or "Assessment"

    # 1. Group responses by (student_id, attempt_number)
    attempts_map = defaultdict(list)
    student_ids = set()
    question_ids = set()
    concept_tags = set()

    for r in responses:
        attempts_map[(r.student_id, r.attempt_number)].append(r)
        student_ids.add(r.student_id)
        question_ids.add(r.question_id)
        if r.concept_tag:
            concept_tags.add(r.concept_tag)

    # Student lookup for names
    student_lookup = {s.usn: s for s in Student.query.filter(Student.usn.in_(student_ids)).all()}
    # StudentTheta lookup
    theta_lookup = {t.usn: t for t in StudentTheta.query.filter(StudentTheta.usn.in_(student_ids)).all()}

    student_attempts = []
    for (s_id, att_num), att_resps in attempts_map.items():
        st_obj = student_lookup.get(s_id)
        first_resp = att_resps[0]
        st_name = (
            st_obj.full_name
            if (st_obj and st_obj.first_name != s_id)
            else (first_resp.student_name if first_resp.student_name else s_id)
        )

        marks_obtained = sum(r.marks_obtained for r in att_resps)
        total_marks = sum(r.question_marks for r in att_resps)
        pct = round((marks_obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
        correct_cnt = sum(1 for r in att_resps if r.is_correct)
        wrong_cnt = sum(1 for r in att_resps if not r.is_correct and not r.is_unanswered)
        unanswered_cnt = sum(1 for r in att_resps if r.is_unanswered)
        sub_time = max((r.submitted_at for r in att_resps if r.submitted_at), default=None)

        theta_rec = theta_lookup.get(s_id)
        theta_val = round(theta_rec.theta, 3) if theta_rec else None

        student_attempts.append({
            "student_id": s_id,
            "student_name": st_name,
            "attempt_number": att_num,
            "marks_obtained": round(marks_obtained, 2),
            "total_marks": round(total_marks, 2),
            "percentage": pct,
            "correct_count": correct_cnt,
            "wrong_count": wrong_cnt,
            "unanswered_count": unanswered_cnt,
            "theta": theta_val,
            "submitted_at": sub_time,
        })

    # Sort student attempts by student_id and attempt_number
    student_attempts.sort(key=lambda a: (a["student_id"], a["attempt_number"]))

    # 2. Overall Summary Metrics
    all_percentages = [a["percentage"] for a in student_attempts]
    all_thetas = [a["theta"] for a in student_attempts if a["theta"] is not None]

    summary = {
        "total_students": len(student_ids),
        "total_attempts": len(student_attempts),
        "average_percentage": round(sum(all_percentages) / len(all_percentages), 2) if all_percentages else 0.0,
        "highest_percentage": max(all_percentages, default=0.0),
        "lowest_percentage": min(all_percentages, default=0.0),
        "average_theta": round(sum(all_thetas) / len(all_thetas), 2) if all_thetas else 0.0,
    }

    # 3. Score Distribution Buckets
    buckets = {"0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 0}
    for p in all_percentages:
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

    # 4. Question-Level IRT Stats
    calibrations = {
        c.question_id: c
        for c in ItemCalibration.query.filter(ItemCalibration.question_id.in_(question_ids)).all()
    }
    question_resps_map = defaultdict(list)
    for r in responses:
        question_resps_map[r.question_id].append(r)

    # Sort questions by their question_no
    sorted_q_ids = sorted(
        question_ids,
        key=lambda q: min(r.question_no for r in question_resps_map[q])
    )

    question_stats = []
    for q_id in sorted_q_ids:
        q_list = question_resps_map[q_id]
        first_q = q_list[0]
        total_q = len(q_list)
        correct_q = sum(1 for r in q_list if r.is_correct)
        acc_pct = round((correct_q / total_q) * 100, 2) if total_q > 0 else 0.0

        cal = calibrations.get(q_id)
        difficulty_b = round(cal.difficulty_b, 3) if cal else 0.0
        std_error = round(cal.std_error, 3) if (cal and cal.std_error is not None) else None
        cal_status = cal.calibration_status if cal else "UNCALIBRATED"

        question_stats.append({
            "question_no": min(r.question_no for r in q_list),
            "question_id": q_id,
            "concept_tag": first_q.concept_tag,
            "bloom_level": first_q.bloom_level or "N/A",
            "total_responses": total_q,
            "correct_responses": correct_q,
            "accuracy_percentage": acc_pct,
            "difficulty_b": difficulty_b,
            "std_error": std_error,
            "calibration_status": cal_status,
        })

    # 5. Concepts Tested Stats (BKT mastery for enrolled students)
    concept_stats = []
    sorted_concepts = sorted(concept_tags)
    for c_tag in sorted_concepts:
        q_count = len({r.question_id for r in responses if r.concept_tag == c_tag})
        st_assessed = len({r.student_id for r in responses if r.concept_tag == c_tag})

        mastery_recs = StudentMastery.query.filter(
            StudentMastery.concept_tag == c_tag,
            StudentMastery.usn.in_(student_ids)
        ).all()

        m_vals = [m.mastery for m in mastery_recs]
        avg_m = round(sum(m_vals) / len(m_vals), 4) if m_vals else 0.0
        mastered_cnt = sum(1 for m in m_vals if m >= 0.70)
        developing_cnt = sum(1 for m in m_vals if 0.40 <= m < 0.70)
        needs_work_cnt = sum(1 for m in m_vals if m < 0.40)

        concept_stats.append({
            "concept_tag": c_tag,
            "question_count": q_count,
            "students_assessed": st_assessed,
            "average_mastery": avg_m,
            "average_percentage": round(avg_m * 100, 1),
            "mastered_count": mastered_cnt,
            "developing_count": developing_cnt,
            "needs_work_count": needs_work_cnt,
        })

    return render_template(
        "teacher/imported_quiz_analytics.html",
        quiz_title=quiz_title,
        subject=subject,
        summary=summary,
        buckets=buckets,
        question_stats=question_stats,
        concept_stats=concept_stats,
        student_attempts=student_attempts,
    )


@teacher_bp.route("/imported-quiz/export/students")
@teacher_required
def export_students_csv():
    """
    Export Student Performance Summary CSV for a selected imported quiz.
    Contains quiz-specific score, marks, percentage, attempts,
    plus cumulative IRT theta and its classification.
    """
    quiz_title = request.args.get("quiz", "").strip()
    if not quiz_title:
        abort(404, description="Quiz parameter is required.")

    responses = (
        ImportedResponse.query.filter_by(quiz=quiz_title)
        .order_by(ImportedResponse.attempt_number.asc(), ImportedResponse.student_id.asc())
        .all()
    )
    if not responses:
        abort(404, description=f"Assessment quiz '{quiz_title}' not found.")

    attempts_map = defaultdict(list)
    student_ids = set()
    for r in responses:
        attempts_map[(r.student_id, r.attempt_number)].append(r)
        student_ids.add(r.student_id)

    student_lookup = {s.usn: s for s in Student.query.filter(Student.usn.in_(student_ids)).all()}
    theta_lookup = {t.usn: t for t in StudentTheta.query.filter(StudentTheta.usn.in_(student_ids)).all()}

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Student_ID_USN",
        "Student_Name",
        "Quiz_Title",
        "Subject",
        "Attempt_Number",
        "Marks_Obtained",
        "Total_Marks",
        "Percentage",
        "Correct_Count",
        "Incorrect_Count",
        "Unanswered_Count",
        "Submission_Timestamp",
        "Cumulative_IRT_Theta",
        "Theta_Classification",
        "Theta_Std_Error",
    ])

    sorted_keys = sorted(attempts_map.keys(), key=lambda k: (k[0], k[1]))
    for (s_id, att_num) in sorted_keys:
        att_resps = attempts_map[(s_id, att_num)]
        first_resp = att_resps[0]
        st_obj = student_lookup.get(s_id)
        st_name = (
            st_obj.full_name
            if (st_obj and st_obj.first_name != s_id)
            else (first_resp.student_name if first_resp.student_name else s_id)
        )

        marks_obtained = round(sum(r.marks_obtained for r in att_resps), 2)
        total_marks = round(sum(r.question_marks for r in att_resps), 2)
        pct = round((marks_obtained / total_marks) * 100, 2) if total_marks > 0 else 0.0
        correct_cnt = sum(1 for r in att_resps if r.is_correct)
        unanswered_cnt = sum(1 for r in att_resps if r.is_unanswered)
        incorrect_cnt = sum(1 for r in att_resps if not r.is_correct and not r.is_unanswered)
        sub_time = max((r.submitted_at for r in att_resps if r.submitted_at), default=None)
        sub_time_str = sub_time.strftime("%Y-%m-%d %H:%M:%S") if sub_time else ""

        theta_rec = theta_lookup.get(s_id)
        theta_val = round(theta_rec.theta, 4) if theta_rec else ""
        theta_class = interpret_theta(theta_rec.theta) if theta_rec else "Not Estimated"
        theta_se = round(theta_rec.std_error, 4) if (theta_rec and theta_rec.std_error is not None) else ""

        writer.writerow([
            s_id,
            st_name,
            quiz_title,
            first_resp.subject,
            att_num,
            marks_obtained,
            total_marks,
            pct,
            correct_cnt,
            incorrect_cnt,
            unanswered_cnt,
            sub_time_str,
            theta_val,
            theta_class,
            theta_se,
        ])

    filename = f"{safe_filename(quiz_title)}_student_performance.csv"
    return FlaskResponse(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@teacher_bp.route("/imported-quiz/export/questions")
@teacher_required
def export_questions_csv():
    """
    Export Question-Level IRT Difficulty Analysis CSV for a selected imported quiz.
    Contains question ID, concept tag, Bloom level, observed quiz accuracy,
    and calibrated IRT difficulty parameter b with standard error.
    """
    quiz_title = request.args.get("quiz", "").strip()
    if not quiz_title:
        abort(404, description="Quiz parameter is required.")

    responses = ImportedResponse.query.filter_by(quiz=quiz_title).all()
    if not responses:
        abort(404, description=f"Assessment quiz '{quiz_title}' not found.")

    question_resps_map = defaultdict(list)
    question_ids = set()
    for r in responses:
        question_resps_map[r.question_id].append(r)
        question_ids.add(r.question_id)

    calibrations = {
        c.question_id: c
        for c in ItemCalibration.query.filter(ItemCalibration.question_id.in_(question_ids)).all()
    }

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Quiz_Title",
        "Question_Number",
        "Question_ID",
        "Concept_Tag",
        "Bloom_Level",
        "Total_Responses",
        "Correct_Responses",
        "Incorrect_Responses",
        "Unanswered_Responses",
        "Accuracy_Percentage",
        "Calibrated_Difficulty_b",
        "Difficulty_Std_Error",
        "Proportion_Correct_p",
        "Calibration_Status",
    ])

    sorted_q_ids = sorted(
        question_ids,
        key=lambda q: min(r.question_no for r in question_resps_map[q])
    )

    for q_id in sorted_q_ids:
        q_list = question_resps_map[q_id]
        first_q = q_list[0]
        q_no = min(r.question_no for r in q_list)
        total_q = len(q_list)
        correct_q = sum(1 for r in q_list if r.is_correct)
        unanswered_q = sum(1 for r in q_list if r.is_unanswered)
        incorrect_q = sum(1 for r in q_list if not r.is_correct and not r.is_unanswered)
        acc_pct = round((correct_q / total_q) * 100, 2) if total_q > 0 else 0.0

        cal = calibrations.get(q_id)
        diff_b = round(cal.difficulty_b, 4) if cal else 0.0
        diff_se = round(cal.std_error, 4) if (cal and cal.std_error is not None) else ""
        p_val = round(cal.proportion_correct, 4) if (cal and cal.proportion_correct is not None) else round(correct_q / total_q, 4) if total_q > 0 else 0.0
        cal_status = cal.calibration_status if cal else "UNCALIBRATED"

        writer.writerow([
            quiz_title,
            q_no,
            q_id,
            first_q.concept_tag,
            first_q.bloom_level or "N/A",
            total_q,
            correct_q,
            incorrect_q,
            unanswered_q,
            acc_pct,
            diff_b,
            diff_se,
            p_val,
            cal_status,
        ])

    filename = f"{safe_filename(quiz_title)}_question_irt_analysis.csv"
    return FlaskResponse(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@teacher_bp.route("/imported-quiz/export/mastery")
@teacher_required
def export_mastery_csv():
    """
    Export Student-Concept BKT Mastery CSV for the students and concepts in this quiz.
    Reflects current BKT knowledge state P(L) based on historical response trajectory.
    """
    quiz_title = request.args.get("quiz", "").strip()
    if not quiz_title:
        abort(404, description="Quiz parameter is required.")

    responses = ImportedResponse.query.filter_by(quiz=quiz_title).all()
    if not responses:
        abort(404, description=f"Assessment quiz '{quiz_title}' not found.")

    student_ids = {r.student_id for r in responses}
    concept_tags = {r.concept_tag for r in responses if r.concept_tag}

    student_lookup = {s.usn: s for s in Student.query.filter(Student.usn.in_(student_ids)).all()}
    name_lookup = {
        r.student_id: r.student_name
        for r in responses
        if r.student_name
    }

    mastery_records = (
        StudentMastery.query.filter(
            StudentMastery.usn.in_(student_ids),
            StudentMastery.concept_tag.in_(concept_tags)
        )
        .order_by(StudentMastery.usn.asc(), StudentMastery.concept_tag.asc())
        .all()
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Student_ID_USN",
        "Student_Name",
        "Quiz_Title",
        "Concept_Tag",
        "Current_BKT_Mastery",
        "Mastery_Percentage",
        "Mastery_Classification",
        "Opportunity_Observations",
        "Correct_Count",
        "Unanswered_Count",
        "Last_Updated",
    ])

    for m in mastery_records:
        st_obj = student_lookup.get(m.usn)
        st_name = (
            st_obj.full_name
            if (st_obj and st_obj.first_name != m.usn)
            else name_lookup.get(m.usn, m.usn)
        )
        m_pct = round(m.mastery * 100, 2)
        m_class = interpret_mastery(m.mastery)
        last_updated_str = m.last_updated.strftime("%Y-%m-%d %H:%M:%S") if m.last_updated else ""

        writer.writerow([
            m.usn,
            st_name,
            quiz_title,
            m.concept_tag,
            round(m.mastery, 4),
            m_pct,
            m_class,
            m.attempts,
            m.correct_count,
            m.unanswered_count,
            last_updated_str,
        ])

    filename = f"{safe_filename(quiz_title)}_bkt_mastery.csv"
    return FlaskResponse(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@teacher_bp.route("/imported-quiz/export/full")
@teacher_required
def export_full_detail_csv():
    """
    Export Full Item-Level Detail CSV for all individual student-question responses
    in the selected quiz, joined with IRT item difficulty, student theta, and concept mastery.
    """
    quiz_title = request.args.get("quiz", "").strip()
    if not quiz_title:
        abort(404, description="Quiz parameter is required.")

    responses = (
        ImportedResponse.query.filter_by(quiz=quiz_title)
        .order_by(
            ImportedResponse.attempt_number.asc(),
            ImportedResponse.student_id.asc(),
            ImportedResponse.question_no.asc()
        )
        .all()
    )
    if not responses:
        abort(404, description=f"Assessment quiz '{quiz_title}' not found.")

    student_ids = {r.student_id for r in responses}
    question_ids = {r.question_id for r in responses}
    concept_tags = {r.concept_tag for r in responses if r.concept_tag}

    student_lookup = {s.usn: s for s in Student.query.filter(Student.usn.in_(student_ids)).all()}
    theta_lookup = {t.usn: t for t in StudentTheta.query.filter(StudentTheta.usn.in_(student_ids)).all()}
    calibrations = {
        c.question_id: c
        for c in ItemCalibration.query.filter(ItemCalibration.question_id.in_(question_ids)).all()
    }
    mastery_lookup = {
        (m.usn, m.concept_tag): m
        for m in StudentMastery.query.filter(
            StudentMastery.usn.in_(student_ids),
            StudentMastery.concept_tag.in_(concept_tags)
        ).all()
    }

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([
        "Student_ID_USN",
        "Student_Name",
        "Quiz_Title",
        "Subject",
        "Attempt_Number",
        "Question_Number",
        "Question_ID",
        "Concept_Tag",
        "Bloom_Level",
        "Calibrated_Difficulty_b",
        "Is_Correct",
        "Is_Unanswered",
        "Selected_Option",
        "Correct_Option",
        "Marks_Obtained",
        "Maximum_Marks",
        "Submission_Timestamp",
        "Cumulative_IRT_Theta",
        "Current_Concept_Mastery",
    ])

    for r in responses:
        st_obj = student_lookup.get(r.student_id)
        st_name = (
            st_obj.full_name
            if (st_obj and st_obj.first_name != r.student_id)
            else (r.student_name if r.student_name else r.student_id)
        )

        cal = calibrations.get(r.question_id)
        diff_b = round(cal.difficulty_b, 4) if cal else ""

        theta_rec = theta_lookup.get(r.student_id)
        theta_val = round(theta_rec.theta, 4) if theta_rec else ""

        m_rec = mastery_lookup.get((r.student_id, r.concept_tag))
        mastery_val = round(m_rec.mastery, 4) if m_rec else ""

        sub_time_str = r.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if r.submitted_at else ""

        writer.writerow([
            r.student_id,
            st_name,
            quiz_title,
            r.subject,
            r.attempt_number,
            r.question_no,
            r.question_id,
            r.concept_tag,
            r.bloom_level or "N/A",
            diff_b,
            1 if r.is_correct else 0,
            1 if r.is_unanswered else 0,
            r.selected_option or "",
            r.correct_option or "",
            r.marks_obtained,
            r.question_marks,
            sub_time_str,
            theta_val,
            mastery_val,
        ])

    filename = f"{safe_filename(quiz_title)}_full_item_detail.csv"
    return FlaskResponse(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

