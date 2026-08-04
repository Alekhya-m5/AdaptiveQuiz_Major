"""
Database models for the Classical Assessment Module.

Design notes (why the schema looks like this):
- Quiz/Question/Attempt/Response are kept normalized but Response also stores a
  denormalized snapshot of concept_tag/topic at answer-time. This means future
  IRT (per-item difficulty/discrimination fit on Question+Response) and BKT
  (per-concept mastery sequences on Response, ordered by timestamp) can be
  computed directly from the `responses` table without joining back through
  Question every time, and remain historically correct even if a teacher edits
  a question after some students have already answered it (published quizzes
  are locked from editing anyway, see routes_teacher.py).
- Attempt stores aggregate score/percentage so the classical module works
  standalone, while Response stores per-item correctness so Phase 2 (IRT/BKT/
  adaptive engine) can be bolted on later purely by reading this table -
  no schema migration required.
- difficulty is optional at data-entry time (teacher may leave it blank) but
  the column already exists so IRT item-difficulty calibration has a home to
  write results back into later if desired.
"""
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Student(db.Model):
    __tablename__ = "students"

    usn = db.Column(db.String(30), primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    course = db.Column(db.String(50), nullable=False, default="RM")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    attempts = db.relationship("Attempt", backref="student", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Teacher(db.Model):
    __tablename__ = "teachers"

    teacher_id = db.Column(db.String(30), primary_key=True)
    first_name = db.Column(db.String(80), nullable=False)
    last_name = db.Column(db.String(80), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    quizzes = db.relationship("Quiz", backref="teacher", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Quiz(db.Model):
    __tablename__ = "quizzes"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    course = db.Column(db.String(50), nullable=False, default="RM")
    teacher_id = db.Column(db.String(30), db.ForeignKey("teachers.teacher_id"), nullable=False)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    published_at = db.Column(db.DateTime, nullable=True)

    questions = db.relationship(
        "Question", backref="quiz", lazy=True,
        cascade="all, delete-orphan", order_by="Question.order_index"
    )
    attempts = db.relationship("Attempt", backref="quiz", lazy=True)

    @property
    def total_marks(self):
        return sum(q.marks for q in self.questions) or 0


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    order_index = db.Column(db.Integer, default=0, nullable=False)
    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500), nullable=False)
    option_d = db.Column(db.String(500), nullable=False)
    correct_option = db.Column(db.String(1), nullable=False)  # 'A' | 'B' | 'C' | 'D'
    concept_tag = db.Column(db.String(120), nullable=False)
    topic = db.Column(db.String(120), nullable=False)
    difficulty = db.Column(db.String(20), nullable=False)  # optional: Easy/Medium/Hard
    difficulty_value = db.Column(db.Float, default=0.0, nullable=False)
    marks = db.Column(db.Float, default=1.0, nullable=False)

    responses = db.relationship("Response", backref="question", lazy=True)

    def option_text(self, letter):
        return {
            "A": self.option_a, "B": self.option_b,
            "C": self.option_c, "D": self.option_d,
        }.get(letter)


class Attempt(db.Model):
    __tablename__ = "attempts"

    id = db.Column(db.Integer, primary_key=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey("quizzes.id"), nullable=False)
    usn = db.Column(db.String(30), db.ForeignKey("students.usn"), nullable=False)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    submitted_at = db.Column(db.DateTime, nullable=True)
    score = db.Column(db.Float, default=0)
    total_marks = db.Column(db.Float, default=0)
    percentage = db.Column(db.Float, default=0)
    correct_count = db.Column(db.Integer, default=0)
    wrong_count = db.Column(db.Integer, default=0)

    responses = db.relationship(
        "Response", backref="attempt", lazy=True, cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.UniqueConstraint("quiz_id", "usn", name="uq_one_attempt_per_student_per_quiz"),
    )


class Response(db.Model):
    """One row per answered question. This is the table Phase 2 (IRT/BKT/
    adaptive engine) will consume directly."""
    __tablename__ = "responses"

    id = db.Column(db.Integer, primary_key=True)
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempts.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    selected_option = db.Column(db.String(1), nullable=True)  # null if unanswered
    is_correct = db.Column(db.Boolean, default=False, nullable=False)
    marks_awarded = db.Column(db.Float, default=0)
    concept_tag = db.Column(db.String(120))  # snapshot at answer-time
    topic = db.Column(db.String(120))        # snapshot at answer-time
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)


class StudentTheta(db.Model):
    __tablename__ = "student_theta"

    usn = db.Column(
        db.String(30),
        db.ForeignKey("students.usn"),
        primary_key=True
    )

    theta = db.Column(db.Float, default=0.0)

    quizzes_attempted = db.Column(db.Integer, default=0)

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    student = db.relationship("Student", backref="theta_record")

class ThetaHistory(db.Model):
    __tablename__ = "theta_history"

    id = db.Column(db.Integer, primary_key=True)

    usn = db.Column(
        db.String(30),
        db.ForeignKey("students.usn"),
        nullable=False
    )

    attempt_id = db.Column(
        db.Integer,
        db.ForeignKey("attempts.id"),
        nullable=False
    )

    question_id = db.Column(
        db.Integer,
        db.ForeignKey("questions.id"),
        nullable=False
    )

    theta_before = db.Column(db.Float, nullable=False)

    theta_after = db.Column(db.Float, nullable=False)

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )