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
        db.String(50),
        db.ForeignKey("students.usn"),
        primary_key=True
    )

    theta = db.Column(db.Float, default=0.0)
    quizzes_attempted = db.Column(db.Integer, default=0)
    std_error = db.Column(db.Float, nullable=True)
    responses_count = db.Column(db.Integer, default=0)
    correct_count = db.Column(db.Integer, default=0)
    estimation_status = db.Column(db.String(40), default="ESTIMATED")
    metadata_json = db.Column(db.Text, nullable=True)

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
        db.String(50),
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


class StudentMastery(db.Model):
    __tablename__ = "student_mastery"

    id = db.Column(db.Integer, primary_key=True)

    usn = db.Column(
        db.String(50),
        db.ForeignKey("students.usn"),
        nullable=False
    )

    concept_tag = db.Column(
        db.String(120),
        nullable=False
    )

    mastery = db.Column(
        db.Float,
        nullable=False,
        default=0.0
    )

    attempts = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    correct_count = db.Column(db.Integer, default=0)
    unanswered_count = db.Column(db.Integer, default=0)
    last_p_slip = db.Column(db.Float, nullable=True)
    last_p_guess = db.Column(db.Float, nullable=True)
    last_p_learn = db.Column(db.Float, nullable=True)
    metadata_json = db.Column(db.Text, nullable=True)

    last_updated = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    __table_args__ = (
        db.UniqueConstraint(
            "usn",
            "concept_tag",
            name="uq_student_concept"
        ),
    )


class ImportBatch(db.Model):
    __tablename__ = "import_batches"

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_hash = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(30), nullable=False, default="PENDING")
    total_rows = db.Column(db.Integer, default=0)
    valid_rows = db.Column(db.Integer, default=0)
    error_rows = db.Column(db.Integer, default=0)
    error_summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    responses = db.relationship(
        "ImportedResponse", backref="batch", lazy=True, cascade="all, delete-orphan"
    )


class ImportedResponse(db.Model):
    """
    Imported assessment response records from external assessment CSVs.
    Separated from manual web-quiz responses to preserve data integrity
    and ensure IRT/BKT model calculations use strictly verified assessment records.
    """
    __tablename__ = "imported_responses"

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey("import_batches.id"), nullable=False, index=True)
    student_id = db.Column(db.String(50), nullable=False, index=True)
    student_name = db.Column(db.String(120), nullable=False)
    quiz = db.Column(db.String(150), nullable=False, index=True)
    subject = db.Column(db.String(100), nullable=False)
    attempt_number = db.Column(db.Integer, nullable=False, default=1)
    question_id = db.Column(db.String(64), nullable=False, index=True)
    question_no = db.Column(db.Integer, nullable=False)
    concept_tag = db.Column(db.String(120), nullable=False, index=True)
    bloom_level = db.Column(db.String(50), nullable=True)
    question_text = db.Column(db.Text, nullable=True)
    option_a = db.Column(db.Text, nullable=True)
    option_b = db.Column(db.Text, nullable=True)
    option_c = db.Column(db.Text, nullable=True)
    option_d = db.Column(db.Text, nullable=True)
    selected_option = db.Column(db.String(20), nullable=True)
    correct_option = db.Column(db.String(20), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    is_unanswered = db.Column(db.Boolean, nullable=False, default=False)
    question_marks = db.Column(db.Float, nullable=False, default=1.0)
    marks_obtained = db.Column(db.Float, nullable=False, default=0.0)
    submitted_at = db.Column(db.DateTime, nullable=False, index=True)
    source = db.Column(db.String(30), nullable=False, default="imported_csv")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint(
            "student_id", "quiz", "attempt_number", "question_id",
            name="uq_imported_student_quiz_attempt_question"
        ),
    )


class ItemCalibration(db.Model):
    """
    Calibrated item difficulty parameters from Rasch / 1PL estimation
    and population response statistics.
    """
    __tablename__ = "item_calibrations"

    id = db.Column(db.Integer, primary_key=True)
    question_id = db.Column(db.String(64), nullable=False, unique=True, index=True)
    quiz = db.Column(db.String(150), nullable=True)
    concept_tag = db.Column(db.String(120), nullable=True, index=True)
    bloom_level = db.Column(db.String(50), nullable=True)
    total_responses = db.Column(db.Integer, default=0, nullable=False)
    correct_responses = db.Column(db.Integer, default=0, nullable=False)
    unanswered_responses = db.Column(db.Integer, default=0, nullable=False)
    proportion_correct = db.Column(db.Float, nullable=True)
    difficulty_b = db.Column(db.Float, default=0.0, nullable=False)
    std_error = db.Column(db.Float, nullable=True)
    calibration_status = db.Column(db.String(40), default="CALIBRATED", nullable=False)
    calibration_method = db.Column(db.String(30), default="PROX", nullable=False)
    metadata_json = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class CourseDocument(db.Model):
    """
    Teacher-uploaded course learning materials (PDF, PPT/PPTX, TXT)
    for RAG retrieval and adaptive question generation.
    """
    __tablename__ = "course_documents"

    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.String(30), db.ForeignKey("teachers.teacher_id"), nullable=True)
    title = db.Column(db.String(255), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(20), nullable=False)  # 'pdf', 'pptx', 'ppt', 'txt'
    file_path = db.Column(db.String(500), nullable=True)
    file_size = db.Column(db.Integer, default=0)
    course = db.Column(db.String(50), nullable=False, default="RM", index=True)
    topic = db.Column(db.String(120), nullable=True)
    total_pages_or_slides = db.Column(db.Integer, default=0)
    total_chunks = db.Column(db.Integer, default=0)
    status = db.Column(db.String(30), default="PROCESSED", nullable=False)  # 'PROCESSED', 'FAILED', 'EMPTY'
    error_message = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    chunks = db.relationship(
        "DocumentChunk", backref="document", lazy=True, cascade="all, delete-orphan",
        order_by="DocumentChunk.chunk_index"
    )


class DocumentChunk(db.Model):
    """
    Extracted, structure-aware text chunks with page/slide metadata
    and persistent embedding representation for semantic retrieval.
    """
    __tablename__ = "document_chunks"

    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("course_documents.id"), nullable=False, index=True)
    chunk_index = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)
    page_number = db.Column(db.Integer, nullable=True)
    slide_number = db.Column(db.Integer, nullable=True)
    course = db.Column(db.String(50), nullable=False, default="RM", index=True)
    topic = db.Column(db.String(120), nullable=True)
    concept_tag = db.Column(db.String(120), nullable=True, index=True)
    token_count = db.Column(db.Integer, default=0)
    embedding_json = db.Column(db.Text, nullable=True)  # JSON-serialized embedding vector
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class GeneratedQuestionRecord(db.Model):
    """
    Quality data collection record for every generated MCQ.
    Captures prompt, retrieved source context, learner parameters (theta/BKT),
    generation output, validation results, and teacher review state.
    Serves as the curated pool for future LoRA fine-tuning datasets.
    """
    __tablename__ = "generated_question_records"

    id = db.Column(db.Integer, primary_key=True)
    course = db.Column(db.String(50), nullable=False, default="RM", index=True)
    topic = db.Column(db.String(120), nullable=True)
    concept_tag = db.Column(db.String(120), nullable=False, index=True)
    learner_theta = db.Column(db.Float, nullable=True)
    learner_mastery = db.Column(db.Float, nullable=True)
    target_difficulty = db.Column(db.String(20), nullable=False, default="Medium")
    target_bloom = db.Column(db.String(50), nullable=True)

    source_context = db.Column(db.Text, nullable=True)
    prompt_text = db.Column(db.Text, nullable=True)
    retrieved_chunk_ids = db.Column(db.Text, nullable=True)  # JSON list of chunk IDs

    question_text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(500), nullable=False)
    option_b = db.Column(db.String(500), nullable=False)
    option_c = db.Column(db.String(500), nullable=False)
    option_d = db.Column(db.String(500), nullable=False)
    correct_option = db.Column(db.String(1), nullable=False)  # 'A', 'B', 'C', 'D'
    explanation = db.Column(db.Text, nullable=False)

    source_document_id = db.Column(db.Integer, db.ForeignKey("course_documents.id"), nullable=True)
    source_filename = db.Column(db.String(255), nullable=True)
    source_page_or_slide = db.Column(db.String(50), nullable=True)

    model_name = db.Column(db.String(100), default="Qwen/Qwen3-0.6B")
    adapter_version = db.Column(db.String(100), default="pilot_lora_v1")

    # Workflow statuses: 'generated', 'validation_failed', 'pending_review', 'approved', 'rejected'
    validation_status = db.Column(db.String(30), default="pending_review", nullable=False, index=True)
    validation_notes = db.Column(db.Text, nullable=True)
    teacher_edited = db.Column(db.Boolean, default=False, nullable=False)
    teacher_notes = db.Column(db.Text, nullable=True)
    quality_score = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(30), nullable=True)
