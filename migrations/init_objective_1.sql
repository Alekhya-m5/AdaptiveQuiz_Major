-- Migration: Objective 1 Schema Updates for Production-Ready IRT + BKT
-- Idempotent script for PostgreSQL (Supabase / Local)

-- 1. Import Batches Table
CREATE TABLE IF NOT EXISTS import_batches (
    id SERIAL PRIMARY KEY,
    filename VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    total_rows INTEGER DEFAULT 0,
    valid_rows INTEGER DEFAULT 0,
    error_rows INTEGER DEFAULT 0,
    error_summary TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    completed_at TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS ix_import_batches_file_hash ON import_batches (file_hash);

-- 2. Imported Assessment Responses Table
CREATE TABLE IF NOT EXISTS imported_responses (
    id SERIAL PRIMARY KEY,
    batch_id INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    student_id VARCHAR(50) NOT NULL,
    student_name VARCHAR(120) NOT NULL,
    quiz VARCHAR(150) NOT NULL,
    subject VARCHAR(100) NOT NULL,
    attempt_number INTEGER NOT NULL DEFAULT 1,
    question_no INTEGER NOT NULL,
    question_id VARCHAR(64) NOT NULL,
    concept_tag VARCHAR(120) NOT NULL,
    bloom_level VARCHAR(50),
    question_text TEXT,
    option_a TEXT,
    option_b TEXT,
    option_c TEXT,
    option_d TEXT,
    selected_option VARCHAR(20),
    correct_option VARCHAR(20) NOT NULL,
    is_correct BOOLEAN NOT NULL DEFAULT FALSE,
    is_unanswered BOOLEAN NOT NULL DEFAULT FALSE,
    question_marks DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    marks_obtained DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    submitted_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    source VARCHAR(30) NOT NULL DEFAULT 'imported_csv',
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_imported_student_quiz_attempt_question UNIQUE (student_id, quiz, attempt_number, question_id)
);

CREATE INDEX IF NOT EXISTS ix_imported_responses_batch_id ON imported_responses (batch_id);
CREATE INDEX IF NOT EXISTS ix_imported_responses_student_id ON imported_responses (student_id);
CREATE INDEX IF NOT EXISTS ix_imported_responses_quiz ON imported_responses (quiz);
CREATE INDEX IF NOT EXISTS ix_imported_responses_question_id ON imported_responses (question_id);
CREATE INDEX IF NOT EXISTS ix_imported_responses_concept_tag ON imported_responses (concept_tag);
CREATE INDEX IF NOT EXISTS ix_imported_responses_submitted_at ON imported_responses (submitted_at);

-- 3. Item Calibrations Table (Rasch / 1PL parameters)
CREATE TABLE IF NOT EXISTS item_calibrations (
    id SERIAL PRIMARY KEY,
    question_id VARCHAR(64) NOT NULL UNIQUE,
    quiz VARCHAR(150),
    concept_tag VARCHAR(120),
    bloom_level VARCHAR(50),
    total_responses INTEGER NOT NULL DEFAULT 0,
    correct_responses INTEGER NOT NULL DEFAULT 0,
    unanswered_responses INTEGER NOT NULL DEFAULT 0,
    proportion_correct DOUBLE PRECISION,
    difficulty_b DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    std_error DOUBLE PRECISION,
    calibration_status VARCHAR(40) NOT NULL DEFAULT 'CALIBRATED',
    calibration_method VARCHAR(30) NOT NULL DEFAULT 'PROX',
    metadata_json TEXT,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_item_calibrations_question_id ON item_calibrations (question_id);
CREATE INDEX IF NOT EXISTS ix_item_calibrations_concept_tag ON item_calibrations (concept_tag);

-- 4. student_theta Table
CREATE TABLE IF NOT EXISTS student_theta (
    usn VARCHAR(50) PRIMARY KEY REFERENCES students(usn),
    theta DOUBLE PRECISION DEFAULT 0.0,
    quizzes_attempted INTEGER DEFAULT 0,
    std_error DOUBLE PRECISION,
    responses_count INTEGER DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    estimation_status VARCHAR(40) DEFAULT 'ESTIMATED',
    metadata_json TEXT,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_theta' AND column_name = 'std_error') THEN
        ALTER TABLE student_theta ADD COLUMN std_error DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_theta' AND column_name = 'responses_count') THEN
        ALTER TABLE student_theta ADD COLUMN responses_count INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_theta' AND column_name = 'correct_count') THEN
        ALTER TABLE student_theta ADD COLUMN correct_count INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_theta' AND column_name = 'estimation_status') THEN
        ALTER TABLE student_theta ADD COLUMN estimation_status VARCHAR(40) DEFAULT 'ESTIMATED';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_theta' AND column_name = 'metadata_json') THEN
        ALTER TABLE student_theta ADD COLUMN metadata_json TEXT;
    END IF;
END $$;

-- 5. theta_history Table
CREATE TABLE IF NOT EXISTS theta_history (
    id SERIAL PRIMARY KEY,
    usn VARCHAR(50) NOT NULL REFERENCES students(usn),
    attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    question_id INTEGER NOT NULL REFERENCES questions(id),
    theta_before DOUBLE PRECISION NOT NULL,
    theta_after DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc')
);

-- 6. student_mastery Table
CREATE TABLE IF NOT EXISTS student_mastery (
    id SERIAL PRIMARY KEY,
    usn VARCHAR(50) NOT NULL REFERENCES students(usn),
    concept_tag VARCHAR(120) NOT NULL,
    mastery DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    attempts INTEGER NOT NULL DEFAULT 0,
    correct_count INTEGER DEFAULT 0,
    unanswered_count INTEGER DEFAULT 0,
    last_p_slip DOUBLE PRECISION,
    last_p_guess DOUBLE PRECISION,
    last_p_learn DOUBLE PRECISION,
    metadata_json TEXT,
    last_updated TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT uq_student_concept UNIQUE (usn, concept_tag)
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'correct_count') THEN
        ALTER TABLE student_mastery ADD COLUMN correct_count INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'unanswered_count') THEN
        ALTER TABLE student_mastery ADD COLUMN unanswered_count INTEGER DEFAULT 0;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'last_p_slip') THEN
        ALTER TABLE student_mastery ADD COLUMN last_p_slip DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'last_p_guess') THEN
        ALTER TABLE student_mastery ADD COLUMN last_p_guess DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'last_p_learn') THEN
        ALTER TABLE student_mastery ADD COLUMN last_p_learn DOUBLE PRECISION;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'student_mastery' AND column_name = 'metadata_json') THEN
        ALTER TABLE student_mastery ADD COLUMN metadata_json TEXT;
    END IF;
END $$;
