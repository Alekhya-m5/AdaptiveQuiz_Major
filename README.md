# Classical Assessment Module (Phase 1)

A deployment-ready quiz system with **Teacher** and **Student** roles.

This project implements the **Classical Assessment Module** for an adaptive learning platform. It collects structured quiz-attempt data that will later be used by the adaptive engine in Phase 2.

The project currently focuses only on quiz management, quiz attempts, grading, and data collection.

---

# Features

## Student

- Student registration and login
- Course selection (currently RM)
- View available quizzes
- Attempt each quiz once
- Automatic evaluation
- View quiz history and results

## Teacher

- Teacher registration and login
- Create quizzes manually
- Add questions with:
  - Question
  - Four options
  - Correct answer
  - Concept tag
  - Topic
  - Difficulty (optional)
  - Marks
- Save quizzes as Draft
- Publish quizzes
- Download student attempt reports in CSV format

## Automatic Grading

After quiz submission, the system automatically calculates:

- Score
- Percentage
- Number of correct answers
- Number of wrong answers

---

# Technology Stack

- Python
- Flask
- Flask-SQLAlchemy
- PostgreSQL (Supabase)
- HTML
- CSS
- Jinja2

---

# Database

The project now uses a shared **Supabase PostgreSQL** database instead of SQLite.

The schema is designed so that future adaptive modules (IRT, BKT, Adaptive Quiz Engine) can directly consume the stored quiz attempt data without requiring database migration.

---

# Project Structure

```text
AdaptiveQuiz/
│
├── app/
│   ├── __init__.py
│   ├── auth.py
│   ├── models.py
│   ├── routes_common.py
│   ├── routes_student.py
│   ├── routes_teacher.py
│   ├── static/
│   └── templates/
│
├── run.py
├── requirements.txt
├── .env.example
├── README.md
└── .gitignore
```

---

# Installation

## 1. Clone the repository

```bash
git clone <repository-url>
cd AdaptiveQuiz
```

---

## 2. Create a virtual environment

macOS/Linux

```bash
python3 -m venv venv
source venv/bin/activate
```

Windows

```bash
python -m venv venv
venv\Scripts\activate
```

---

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

---

## 4. Configure Environment Variables

Copy the example file.

macOS/Linux

```bash
cp .env.example .env
```

Windows

```cmd
copy .env.example .env
```

Edit `.env` and update the following values.

```env
SECRET_KEY=your-secret-key

DATABASE_URL=postgresql+psycopg2://postgres.<project-ref>:YOUR_DATABASE_PASSWORD@aws-1-ap-south-1.pooler.supabase.com:5432/postgres
```

Replace:

- `YOUR_DATABASE_PASSWORD` with the shared Supabase database password.

---

## 5. Run the application

```bash
python run.py
```

Open:

```
http://localhost:5000
```

---

# Usage

### Teacher

1. Register as a teacher.
2. Log in.
3. Create a quiz.
4. Add questions.
5. Save as Draft.
6. Publish the quiz.
7. Download quiz reports as CSV.

### Student

1. Register as a student.
2. Log in.
3. Attempt available quizzes.
4. View quiz results.

---

# CSV Report

Teachers can download reports containing:

- Student USN
- Student Name
- Quiz ID
- Question ID
- Selected Option
- Correct Option
- Correct/Incorrect
- Concept Tag
- Topic
- Marks
- Final Score
- Timestamp

---

# Phase 2 (Future Work)

The following modules will be integrated in the next phase:

- Item Response Theory (IRT)
- Bayesian Knowledge Tracing (BKT)
- Adaptive Quiz Generation
- Ability (Theta) Estimation
- Mastery Prediction
- Personalized Learning Path
- AI-powered Question Generation
- Weak Topic Detection

---

# Contributors

Developed as part of the Adaptive Learning Platform project.