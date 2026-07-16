# Classical Assessment Module (Phase 1)

A working, deployment-ready quiz system with **Teacher** and **Student** roles.
This is the *classical* assessment module only — it collects clean, structured
quiz-attempt data. There is no adaptive engine, no AI-generated questions, no
theta/mastery/ability scoring, and no LLM usage anywhere in this codebase.
That is intentionally left for Phase 2, which will read from this database.

## What's included

- Student registration/login (USN + password), course selection (currently
  only "RM"), quiz taking, and a personal result history.
- Teacher registration/login (Teacher ID + password), manual quiz creation
  (question, 4 options, correct answer, concept tag, topic, optional
  difficulty, marks), draft/publish workflow, and a CSV log-report download
  per quiz.
- Automatic grading on submission (score, percentage, correct/wrong counts).
- A schema (see "Database design" below) that Phase 2 (IRT / BKT / adaptive
  engine) can consume directly without any migration.

## Running with Docker (recommended)

```bash
docker compose up --build
```

Then open **http://localhost:5000**. Data persists in a Docker volume
(`quiz_data`) across restarts.

To stop: `docker compose down` (add `-v` to also wipe the database volume).

## Running locally without Docker

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

Open **http://localhost:5000**. The SQLite database is created automatically
at `instance/quiz_system.db` on first run.

## How to use it

1. Go to **Teacher Register**, create a teacher account, log in.
2. Click **Create Quiz** → give it a title (e.g. "Quiz 1"), pick the course
   (RM), add one or more questions manually (question text, 4 options,
   correct option, concept tag, topic, optional difficulty, marks) → **Save
   Quiz as Draft**.
3. On the quiz's manage page, click **Publish Quiz**. Once published, a quiz
   is locked — it can no longer be edited, only reported on — so that
   historical data always matches what students actually saw.
4. Go to **Student Register**, create a student account with a USN, choose
   course RM, log in.
5. The student dashboard lists every published quiz for their course. They
   take a quiz once (one attempt per student per quiz is enforced at the
   database level) and immediately see their score.
6. Back on the teacher's quiz page, **Download CSV Report** produces a row
   per (student, question) pair with:
   `USN, Student Name, Quiz ID, Question ID, Selected Option, Correct Option,
   Correct/Incorrect, Concept Tag, Topic, Marks, Final Score, Timestamp`



## Explicitly NOT implemented (belongs to Phase 2)

Theta / ability estimation, knowledge tracing, mastery scores, adaptive
question selection, weak-topic detection, LLM-generated questions,
OpenRouter, semantic retrieval, OCR, embeddings.

## Project structure

```
quiz_system/
├── app/
│   ├── __init__.py          # app factory
│   ├── models.py            # SQLAlchemy models
│   ├── auth.py               # login-required decorators
│   ├── routes_common.py      # landing page, logout
│   ├── routes_student.py     # student register/login/dashboard/quiz/results
│   ├── routes_teacher.py     # teacher register/login/dashboard/quiz CRUD/report
│   ├── templates/
│   └── static/css/style.css
├── run.py
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```
