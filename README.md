# Adaptive Learning System

An end-to-end adaptive assessment and AI-powered learning platform with **Psychometric Analytics (IRT)**, **Bayesian Knowledge Tracing (BKT)**, and **RAG-based Question Generation (Qwen + LoRA)**.

---

## Architecture & Features

### 1. Assessment & Data Ingestion
- **Standard Quizzes**: Student and teacher roles for creating, publishing, taking, and evaluating quizzes.
- **Weekly Ingestion Pipeline**: Ingestion of historical and weekly assessment CSV files (e.g. Unit-1 and Unit-2) with SHA256 file-level and composite-key record-level deduplication.
- **Bundled Datasets**: Official assessment CSV files are version-controlled under `data/assessments/`.

### 2. Psychometric & Adaptive Modeling
- **Rasch Item Response Theory (1PL IRT)**:
  - Joint Estimation / JMLE for calibrating question difficulty ($b$) and cumulative student ability ($\theta$).
  - Mean-centered item difficulty normalization ($\sum b = 0$).
  - Supports pooling attempts across multiple quizzes (Unit-1 and Unit-2).
- **Bayesian Knowledge Tracing (BKT)**:
  - Tracks student concept-level mastery probabilities $P(L_t)$.
  - Configurable slip, guess, transition, and standard initial prior ($P(L_0) = 0.20$).
  - Incremental online updates upon quiz completion as well as batch recomputation.

### 3. Analytics & Export Pipeline
- **Student Analytics**: Real-time feedback displaying IRT ability score ($\theta$), percentile rank, and concept mastery badges.
- **Teacher Dashboard**: Cohort analytics, item difficulty distribution, Bloom's taxonomy breakdown, and student mastery profiles.
- **Consolidated Learner-Profile CSV Export**: Generates one-row-per-student-concept exports tailored for SLM / LoRA personalization.

### 4. RAG & LoRA Adaptive Question Generation
- **Course Document Ingestion**: Supports uploading PDF, PPTX, and TXT materials with automatic chunking and TF-IDF / vector retrieval.
- **Singleton Model Service**: Loads Qwen base model (`Qwen/Qwen3-0.6B`) and attaches the fine-tuned LoRA adapter once in memory.
- **Teacher Review Workflow**: Allows instructors to review, edit, approve, or reject AI-generated adaptive questions.

---

## Project Structure

```text
AdaptiveQuiz/
├── app/
│   ├── ai/                  # RAG, Qwen+LoRA singleton service, adaptive generator
│   ├── bkt/                 # Bayesian Knowledge Tracing engine & services
│   ├── ingestion/           # CSV parser, validator, and transactional ingestion
│   ├── irt/                 # 1PL Rasch IRT calibration & theta estimation
│   ├── models.py            # SQLAlchemy database models
│   ├── pipeline.py          # Unified assessment pipeline orchestrator
│   ├── cli.py               # CLI commands for data ingestion & model recalculation
│   ├── routes_common.py     # Auth & landing routes
│   ├── routes_student.py    # Student quiz taking, results, & analytics
│   ├── routes_teacher.py    # Teacher dashboards, analytics, review, & CSV exports
│   ├── static/              # CSS, JavaScript, and asset files
│   └── templates/           # Jinja2 HTML templates
├── data/
│   └── assessments/         # Official assessment CSVs (Unit-1, Unit-2)
├── tests/                   # Automated unit & integration test suite (58+ tests)
├── .env.example             # Template for environment variables
├── requirements.txt         # Project dependencies
├── run.py                   # Application entry point
└── README.md
```

---

## Step-by-Step Setup Guide

Follow these steps to set up and run the project from scratch on any computer.

### 1. Clone the Repository & Checkout Branch

```bash
git clone https://github.com/BhagyashreeSM5/AdaptiveQuiz.git
cd AdaptiveQuiz

# Checkout the active feature branch with IRT, BKT, Unit 1 & Unit 2 support:
git checkout feature/student-analytics-irt-bkt
```

*(Note: If the branch has already been merged into `main`, you can stay on `main` and run `git pull origin main`.)*

---

### 2. Set Up Python Virtual Environment

**macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```

**Windows (Command Prompt / PowerShell):**
```cmd
python -m venv venv
venv\Scripts\activate
```

---

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### 4. Configure Environment Variables

Create your local `.env` configuration file from the template:

**macOS / Linux:**
```bash
cp .env.example .env
```

**Windows:**
```cmd
copy .env.example .env
```

Open `.env` in an editor and set your database connection URL:

```env
SECRET_KEY=your-secret-key-change-in-production

# Example 1: Local PostgreSQL database
DATABASE_URL=postgresql://localhost/adaptivequiz

# Example 2: Shared Supabase PostgreSQL database
# DATABASE_URL=postgresql+psycopg2://postgres.<project-ref>:YOUR_PASSWORD@aws-1-ap-south-1.pooler.supabase.com:5432/postgres

# Optional: Path to fine-tuned LoRA adapter (if testing local SLM generation)
# LORA_ADAPTER_PATH=models/fine_tuned_qwen_lora
```

*Note: Database tables are automatically created on first startup via `db.create_all()`.*

---

### 5. Ingest Unit 1 & Unit 2 Assessment Data

Run the assessment initialization command. This automatically discovers all assessment CSV files in `data/assessments/` (Unit-1 and Unit-2), ingests the student responses, and calibrates the IRT and BKT models:

```bash
python -m app.cli import-all-assessments
```

**Output should confirm:**
- Ingested `Unit-1_Quiz_02-09-2026_Detailed_Results.csv` (2,420 rows, 64 students, 20 questions)
- Ingested `Unit-2_18-09-2026_Detailed_Results.csv` (2,100 rows, 60 students, 20 questions)
- IRT: 40 items calibrated across 68 students
- BKT: 1,304 student-concept mastery pairs updated

> **Idempotent:** You can safely run this command at any time. It skips previously imported files and will not create duplicate responses or corrupted data.

---

### 6. Verify Database Status

Check the database summary to ensure all batches and calibrations are loaded:

```bash
python -m app.cli show-summary
```

---

### 7. Run the Application

Start the Flask development server:

```bash
python run.py
```

Open your browser and navigate to:
```
http://localhost:5000
```

- **Teacher Dashboard & Analytics**: `/teacher/cohort-analytics` or `/teacher/dashboard`
- **Learner Profile Export**: `/teacher/export/learner-profile-csv`
- **Student Dashboard**: `/student/dashboard`

---

### 8. Run Automated Tests

Run the full automated test suite to verify pipeline integrity:

```bash
python -m unittest discover tests
```

All 58+ unit and integration tests should pass.

---

## CLI Reference

The CLI supports both `python -m app.cli <command>` and Flask CLI `flask <command>`:

| Command | Description |
|---|---|
| `python -m app.cli import-all-assessments` | Ingests all CSVs in `data/assessments/` and recalculates IRT/BKT models. |
| `python -m app.cli run-pipeline <csv-path>` | Runs end-to-end ingestion and calibration for a single CSV file. |
| `python -m app.cli import-csv <csv-path>` | Imports responses from a CSV file without recalculating models. |
| `python -m app.cli recalculate-models` | Recalculates IRT difficulty/theta and BKT mastery on existing database records. |
| `python -m app.cli show-summary` | Displays summary of batches, calibrations, students, and mastery records. |
