"""
Comprehensive test suite for Teacher Dashboard and Analytics features:
- Authentication & access control
- Imported assessment quizzes listing on dashboard
- Quiz-specific visual and tabular analytics view
- Modular CSV exports (Student Performance, Question IRT, BKT Mastery, Full Detail)
- Correct data semantics (cumulative theta vs quiz scores, attempt grouping)
- Filtering across multiple quizzes
- Missing data & empty/non-existent quiz handling
- BKT recalculation idempotency
"""

import csv
import io
import unittest
from datetime import datetime

from app import create_app
from app.models import (
    db,
    Teacher,
    Student,
    ImportBatch,
    ImportedResponse,
    ItemCalibration,
    StudentTheta,
    StudentMastery,
)
from app.pipeline import AdaptivePipeline


class TestTeacherDashboard(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key-teacher",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Seed test teacher
        self.teacher = Teacher(
            teacher_id="T_001",
            first_name="Prof",
            last_name="Turing"
        )
        self.teacher.set_password("SecurePass123")
        db.session.add(self.teacher)

        # Seed students
        self.student1 = Student(
            usn="USN_ALICE",
            first_name="Alice",
            last_name="Smith",
            course="RM"
        )
        self.student1.set_password("pass")

        self.student2 = Student(
            usn="USN_BOB",
            first_name="Bob",
            last_name="Jones",
            course="RM"
        )
        self.student2.set_password("pass")
        db.session.add_all([self.student1, self.student2])

        # Seed an ImportBatch
        self.batch = ImportBatch(
            filename="unit_test_assessment.csv",
            file_hash="mockhash123456",
            status="SUCCESS",
            total_rows=6,
            valid_rows=6
        )
        db.session.add(self.batch)
        db.session.flush()

        # Seed Quiz 1 responses (Alice attempt 1 & 2, Bob attempt 1)
        # Quiz 1 has 2 questions: Q1 (Basics) and Q2 (Hypothesis)
        self.responses_quiz1 = [
            # Alice Attempt 1 on Quiz 1
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_ALICE",
                student_name="Alice Smith",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=1,
                question_id="Q_U1_01",
                question_no=1,
                concept_tag="Basics",
                bloom_level="Remember",
                question_marks=1.0,
                marks_obtained=1.0,
                is_correct=True,
                is_unanswered=False,
                selected_option="A",
                correct_option="A",
                submitted_at=datetime(2026, 9, 2, 8, 30, 0),
            ),
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_ALICE",
                student_name="Alice Smith",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=1,
                question_id="Q_U1_02",
                question_no=2,
                concept_tag="Hypothesis",
                bloom_level="Apply",
                question_marks=1.0,
                marks_obtained=0.0,
                is_correct=False,
                is_unanswered=False,
                selected_option="C",
                correct_option="B",
                submitted_at=datetime(2026, 9, 2, 8, 30, 0),
            ),
            # Alice Attempt 2 on Quiz 1 (Retake)
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_ALICE",
                student_name="Alice Smith",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=2,
                question_id="Q_U1_01",
                question_no=1,
                concept_tag="Basics",
                bloom_level="Remember",
                question_marks=1.0,
                marks_obtained=1.0,
                is_correct=True,
                is_unanswered=False,
                selected_option="A",
                correct_option="A",
                submitted_at=datetime(2026, 9, 2, 9, 30, 0),
            ),
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_ALICE",
                student_name="Alice Smith",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=2,
                question_id="Q_U1_02",
                question_no=2,
                concept_tag="Hypothesis",
                bloom_level="Apply",
                question_marks=1.0,
                marks_obtained=1.0,
                is_correct=True,
                is_unanswered=False,
                selected_option="B",
                correct_option="B",
                submitted_at=datetime(2026, 9, 2, 9, 30, 0),
            ),
            # Bob Attempt 1 on Quiz 1 (with unanswered question)
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_BOB",
                student_name="Bob Jones",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=1,
                question_id="Q_U1_01",
                question_no=1,
                concept_tag="Basics",
                bloom_level="Remember",
                question_marks=1.0,
                marks_obtained=1.0,
                is_correct=True,
                is_unanswered=False,
                selected_option="A",
                correct_option="A",
                submitted_at=datetime(2026, 9, 2, 8, 35, 0),
            ),
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_BOB",
                student_name="Bob Jones",
                quiz="Unit-1 Assessment",
                subject="RM",
                attempt_number=1,
                question_id="Q_U1_02",
                question_no=2,
                concept_tag="Hypothesis",
                bloom_level="Apply",
                question_marks=1.0,
                marks_obtained=0.0,
                is_correct=False,
                is_unanswered=True,
                selected_option="",
                correct_option="B",
                submitted_at=datetime(2026, 9, 2, 8, 35, 0),
            ),
        ]
        db.session.add_all(self.responses_quiz1)

        # Seed Quiz 2 responses (Unit-2 Assessment) to test quiz filtering
        self.responses_quiz2 = [
            ImportedResponse(
                batch_id=self.batch.id,
                student_id="USN_ALICE",
                student_name="Alice Smith",
                quiz="Unit-2 Assessment",
                subject="RM",
                attempt_number=1,
                question_id="Q_U2_01",
                question_no=1,
                concept_tag="Sampling",
                bloom_level="Understand",
                question_marks=1.0,
                marks_obtained=1.0,
                is_correct=True,
                is_unanswered=False,
                selected_option="D",
                correct_option="D",
                submitted_at=datetime(2026, 9, 18, 10, 0, 0),
            )
        ]
        db.session.add_all(self.responses_quiz2)

        # Seed IRT calibrations
        self.cal_q1 = ItemCalibration(
            question_id="Q_U1_01",
            quiz="Unit-1 Assessment",
            concept_tag="Basics",
            bloom_level="Remember",
            total_responses=3,
            correct_responses=3,
            unanswered_responses=0,
            proportion_correct=1.0,
            difficulty_b=-1.25,
            std_error=0.45,
            calibration_status="CALIBRATED",
        )
        self.cal_q2 = ItemCalibration(
            question_id="Q_U1_02",
            quiz="Unit-1 Assessment",
            concept_tag="Hypothesis",
            bloom_level="Apply",
            total_responses=3,
            correct_responses=1,
            unanswered_responses=1,
            proportion_correct=0.33,
            difficulty_b=0.85,
            std_error=0.52,
            calibration_status="CALIBRATED",
        )
        db.session.add_all([self.cal_q1, self.cal_q2])

        # Seed StudentTheta
        self.theta_alice = StudentTheta(
            usn="USN_ALICE",
            theta=1.35,
            std_error=0.42,
            estimation_status="CONVERGED",
            responses_count=5,
            correct_count=4,
        )
        self.theta_bob = StudentTheta(
            usn="USN_BOB",
            theta=-0.45,
            std_error=0.58,
            estimation_status="CONVERGED",
            responses_count=2,
            correct_count=1,
        )
        db.session.add_all([self.theta_alice, self.theta_bob])

        # Seed StudentMastery
        self.mastery_alice_basics = StudentMastery(
            usn="USN_ALICE",
            concept_tag="Basics",
            mastery=0.88,
            attempts=2,
            correct_count=2,
            unanswered_count=0,
            last_updated=datetime(2026, 9, 2, 9, 30, 0),
        )
        self.mastery_alice_hyp = StudentMastery(
            usn="USN_ALICE",
            concept_tag="Hypothesis",
            mastery=0.62,
            attempts=2,
            correct_count=1,
            unanswered_count=0,
            last_updated=datetime(2026, 9, 2, 9, 30, 0),
        )
        self.mastery_bob_basics = StudentMastery(
            usn="USN_BOB",
            concept_tag="Basics",
            mastery=0.75,
            attempts=1,
            correct_count=1,
            unanswered_count=0,
            last_updated=datetime(2026, 9, 2, 8, 35, 0),
        )
        self.mastery_bob_hyp = StudentMastery(
            usn="USN_BOB",
            concept_tag="Hypothesis",
            mastery=0.20,
            attempts=1,
            correct_count=0,
            unanswered_count=1,
            last_updated=datetime(2026, 9, 2, 8, 35, 0),
        )
        db.session.add_all([
            self.mastery_alice_basics,
            self.mastery_alice_hyp,
            self.mastery_bob_basics,
            self.mastery_bob_hyp,
        ])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login_teacher(self):
        with self.client.session_transaction() as sess:
            sess["role"] = "teacher"
            sess["user_id"] = "T_001"

    # -------------------------------------------------------------
    # 1. Teacher Authorization
    # -------------------------------------------------------------
    def test_teacher_authorization_required(self):
        """Unauthenticated requests must redirect to teacher login."""
        protected_endpoints = [
            "/teacher/dashboard",
            "/teacher/imported-quiz/analytics?quiz=Unit-1+Assessment",
            "/teacher/imported-quiz/export/students?quiz=Unit-1+Assessment",
            "/teacher/imported-quiz/export/questions?quiz=Unit-1+Assessment",
            "/teacher/imported-quiz/export/mastery?quiz=Unit-1+Assessment",
            "/teacher/imported-quiz/export/learner-profiles?quiz=Unit-1+Assessment",
            "/teacher/imported-quiz/export/full?quiz=Unit-1+Assessment",
            "/teacher/export/learner-profiles",
        ]
        for url in protected_endpoints:
            res = self.client.get(url, follow_redirects=False)
            self.assertEqual(
                res.status_code, 302,
                f"Expected 302 redirect for unauthenticated access to {url}"
            )
            self.assertIn("/teacher/login", res.headers.get("Location", ""))

    # -------------------------------------------------------------
    # 2. Teacher Dashboard Lists Imported Quizzes
    # -------------------------------------------------------------
    def test_teacher_dashboard_lists_imported_quizzes(self):
        """Dashboard must display curriculum assessment quizzes with real DB stats."""
        self._login_teacher()
        res = self.client.get("/teacher/dashboard")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        # Check for presence of Unit-1 and Unit-2 assessment quizzes
        self.assertIn("Unit-1 Assessment", html)
        self.assertIn("Unit-2 Assessment", html)
        self.assertIn("Curriculum Assessment Quizzes", html)
        self.assertIn("View Analytics", html)
        self.assertIn("Export CSV", html)
        self.assertIn("View Full Cohort Analytics", html)

    # -------------------------------------------------------------
    # 3. Analytics View & Aggregations
    # -------------------------------------------------------------
    def test_imported_quiz_analytics_view(self):
        """Analytics page must render KPI cards, charts, and tables for the selected quiz."""
        self._login_teacher()
        res = self.client.get("/teacher/imported-quiz/analytics?quiz=Unit-1+Assessment")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)

        self.assertIn("Unit-1 Assessment", html)
        self.assertIn("Quiz Analytics", html)
        self.assertIn("Assessed Students", html)
        self.assertIn("USN_ALICE", html)
        self.assertIn("USN_BOB", html)
        self.assertIn("Q_U1_01", html)
        self.assertIn("Q_U1_02", html)
        self.assertIn("Basics", html)
        self.assertIn("Hypothesis", html)
        # Check charts elements
        self.assertIn("scoreDistributionChart", html)
        self.assertIn("itemDifficultyChart", html)
        self.assertIn("conceptMasteryChart", html)

    # -------------------------------------------------------------
    # 4. Quiz Filtering Across Multiple Quizzes
    # -------------------------------------------------------------
    def test_quiz_filtering_and_multiple_quizzes(self):
        """Analytics for Unit-1 must NOT include Unit-2 questions or attempts."""
        self._login_teacher()
        res = self.client.get("/teacher/imported-quiz/analytics?quiz=Unit-1+Assessment")
        html = res.get_data(as_text=True)
        self.assertIn("Q_U1_01", html)
        self.assertNotIn("Q_U2_01", html)
        self.assertNotIn("Sampling", html)

        # Conversely, Unit-2 must only show Unit-2 question
        res2 = self.client.get("/teacher/imported-quiz/analytics?quiz=Unit-2+Assessment")
        html2 = res2.get_data(as_text=True)
        self.assertIn("Q_U2_01", html2)
        self.assertIn("Sampling", html2)
        self.assertNotIn("Q_U1_01", html2)

    # -------------------------------------------------------------
    # 5. Student Performance Summary CSV Content & Semantics
    # -------------------------------------------------------------
    def test_export_students_csv(self):
        """Student performance CSV must correctly reflect quiz scores and cumulative theta."""
        self._login_teacher()
        res = self.client.get("/teacher/imported-quiz/export/students?quiz=Unit-1+Assessment")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("Content-Type", ""))
        self.assertIn("attachment", res.headers.get("Content-Disposition", ""))
        self.assertIn("Unit-1_Assessment_student_performance.csv", res.headers.get("Content-Disposition", ""))

        csv_text = res.get_data(as_text=True)
        reader = list(csv.reader(io.StringIO(csv_text)))
        headers = reader[0]

        expected_headers = [
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
        ]
        self.assertEqual(headers, expected_headers)

        # 3 student-attempts: Alice #1, Alice #2, Bob #1
        data_rows = reader[1:]
        self.assertEqual(len(data_rows), 3)

        # Verify Alice attempt 1: 1/2 = 50.0%
        row_alice_1 = [r for r in data_rows if r[0] == "USN_ALICE" and r[4] == "1"][0]
        self.assertEqual(row_alice_1[5], "1.0")
        self.assertEqual(row_alice_1[6], "2.0")
        self.assertEqual(row_alice_1[7], "50.0")
        self.assertEqual(row_alice_1[8], "1")  # correct
        self.assertEqual(row_alice_1[9], "1")  # incorrect
        self.assertEqual(row_alice_1[10], "0") # unanswered
        self.assertEqual(row_alice_1[12], "1.35") # cumulative theta
        self.assertEqual(row_alice_1[13], "Proficient (+1.0 to +2.0)")

        # Verify Alice attempt 2 (retake): 2/2 = 100.0%
        row_alice_2 = [r for r in data_rows if r[0] == "USN_ALICE" and r[4] == "2"][0]
        self.assertEqual(row_alice_2[5], "2.0")
        self.assertEqual(row_alice_2[6], "2.0")
        self.assertEqual(row_alice_2[7], "100.0")

        # Verify Bob attempt 1: 1/2 = 50.0% with 1 unanswered
        row_bob = [r for r in data_rows if r[0] == "USN_BOB"][0]
        self.assertEqual(row_bob[8], "1")
        self.assertEqual(row_bob[9], "0")
        self.assertEqual(row_bob[10], "1")
        self.assertEqual(row_bob[12], "-0.45")
        self.assertEqual(row_bob[13], "Beginning (-1.0 to 0.0)")

    # -------------------------------------------------------------
    # 6. Question-Level IRT Difficulty CSV
    # -------------------------------------------------------------
    def test_export_questions_csv(self):
        """Question IRT CSV must export question details and calibrated parameters."""
        self._login_teacher()
        res = self.client.get("/teacher/imported-quiz/export/questions?quiz=Unit-1+Assessment")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/csv", res.headers.get("Content-Type", ""))

        csv_text = res.get_data(as_text=True)
        reader = list(csv.reader(io.StringIO(csv_text)))
        headers = reader[0]

        expected_headers = [
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
        ]
        self.assertEqual(headers, expected_headers)

        rows = reader[1:]
        self.assertEqual(len(rows), 2)

        q1_row = [r for r in rows if r[2] == "Q_U1_01"][0]
        self.assertEqual(q1_row[3], "Basics")
        self.assertEqual(q1_row[10], "-1.25")
        self.assertEqual(q1_row[13], "CALIBRATED")

        q2_row = [r for r in rows if r[2] == "Q_U1_02"][0]
        self.assertEqual(q2_row[3], "Hypothesis")
        self.assertEqual(q2_row[10], "0.85")

    # -------------------------------------------------------------
    # 7. Student-Concept BKT Mastery CSV
    # -------------------------------------------------------------
    def test_export_mastery_csv(self):
        """BKT mastery CSV must reflect student-concept pairs for the quiz concepts."""
        self._login_teacher()
        res = self.client.get("/teacher/imported-quiz/export/mastery?quiz=Unit-1+Assessment")
        self.assertEqual(res.status_code, 200)

        csv_text = res.get_data(as_text=True)
        reader = list(csv.reader(io.StringIO(csv_text)))
        headers = reader[0]

        expected_headers = [
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
        ]
        self.assertEqual(headers, expected_headers)

        rows = reader[1:]
        # 4 records: Alice (Basics, Hypothesis), Bob (Basics, Hypothesis)
        self.assertEqual(len(rows), 4)

        alice_basics = [r for r in rows if r[0] == "USN_ALICE" and r[3] == "Basics"][0]
        self.assertEqual(alice_basics[4], "0.88")
        self.assertEqual(alice_basics[5], "88.0")
        self.assertEqual(alice_basics[6], "Mastered (>= 70%)")

        bob_hyp = [r for r in rows if r[0] == "USN_BOB" and r[3] == "Hypothesis"][0]
        self.assertEqual(bob_hyp[4], "0.2")
        self.assertEqual(bob_hyp[6], "Needs Work (< 40%)")

    # -------------------------------------------------------------
    # 8. Consolidated Learner Personalization Profiles CSV
    # -------------------------------------------------------------
    def test_export_learner_profiles_csv(self):
        """Learner profiles CSV must export one row per student-concept pair with theta and mastery, no PII."""
        self._login_teacher()
        
        # Test 1: Quiz-scoped export via /teacher/imported-quiz/export/learner-profiles
        res = self.client.get("/teacher/imported-quiz/export/learner-profiles?quiz=Unit-1+Assessment")
        self.assertEqual(res.status_code, 200)

        csv_text = res.get_data(as_text=True)
        reader = list(csv.reader(io.StringIO(csv_text)))
        headers = reader[0]

        expected_headers = [
            "student_key",
            "subject_or_course",
            "irt_theta",
            "concept_tag",
            "bkt_mastery",
            "bkt_attempts",
            "bkt_correct_count",
        ]
        self.assertEqual(headers, expected_headers)

        rows = reader[1:]
        # Exactly 4 student-concept rows (2 students x 2 concepts in Unit-1), NOT 6 question rows
        self.assertEqual(len(rows), 4)

        # Confirm no PII appears anywhere in the output
        for r in rows:
            row_str = " ".join(r)
            self.assertNotIn("USN_ALICE", row_str)
            self.assertNotIn("USN_BOB", row_str)
            self.assertNotIn("Alice", row_str)
            self.assertNotIn("Bob", row_str)
            # student_key must match STUDENT_<hash>
            self.assertTrue(r[0].startswith("STUDENT_"))

        # Verify Alice's records (theta = 1.35 repeated on both concepts)
        alice_key = rows[0][0]  # sorted by student_key
        # Check that unique student keys count is 2
        unique_keys = set(r[0] for r in rows)
        self.assertEqual(len(unique_keys), 2)

        # Check legacy endpoint alias /teacher/imported-quiz/export/full works identically
        res_legacy = self.client.get("/teacher/imported-quiz/export/full?quiz=Unit-1+Assessment")
        self.assertEqual(res_legacy.status_code, 200)
        self.assertEqual(res_legacy.get_data(as_text=True), csv_text)

        # Test 2: Global cohort export via /teacher/export/learner-profiles (no quiz param)
        res_global = self.client.get("/teacher/export/learner-profiles")
        self.assertEqual(res_global.status_code, 200)
        reader_global = list(csv.reader(io.StringIO(res_global.get_data(as_text=True))))
        self.assertEqual(reader_global[0], expected_headers)
        self.assertEqual(len(reader_global[1:]), 4)

    # -------------------------------------------------------------
    # 9. Missing Data & Empty / Non-Existent Quiz Handling
    # -------------------------------------------------------------
    def test_empty_or_nonexistent_quiz_handling(self):
        """Accessing missing or nonexistent quizzes must return 404 cleanly."""
        self._login_teacher()

        # Non-existent quiz in analytics
        res = self.client.get("/teacher/imported-quiz/analytics?quiz=GhostQuiz")
        self.assertEqual(res.status_code, 404)

        # Missing quiz query parameter in analytics
        res = self.client.get("/teacher/imported-quiz/analytics")
        self.assertEqual(res.status_code, 404)

        # Non-existent quiz in export
        res = self.client.get("/teacher/imported-quiz/export/students?quiz=GhostQuiz")
        self.assertEqual(res.status_code, 404)

        res = self.client.get("/teacher/imported-quiz/export/questions?quiz=GhostQuiz")
        self.assertEqual(res.status_code, 404)

        res = self.client.get("/teacher/imported-quiz/export/mastery?quiz=GhostQuiz")
        self.assertEqual(res.status_code, 404)

        res = self.client.get("/teacher/imported-quiz/export/full?quiz=GhostQuiz")
        self.assertEqual(res.status_code, 404)

    def test_missing_data_resilience(self):
        """Student with no theta or question with no calibration must render without crashing."""
        self._login_teacher()

        # Add a student with no theta estimate and uncalibrated question in Unit-3
        new_resp = ImportedResponse(
            batch_id=self.batch.id,
            student_id="USN_CHARLIE",
            student_name="Charlie Brown",
            quiz="Unit-3 Assessment",
            subject="RM",
            attempt_number=1,
            question_id="Q_U3_99",
            question_no=1,
            concept_tag="NewConcept",
            bloom_level="Create",
            question_marks=2.0,
            marks_obtained=2.0,
            is_correct=True,
            is_unanswered=False,
            selected_option="A",
            correct_option="A",
            submitted_at=datetime(2026, 10, 1, 12, 0, 0),
        )
        db.session.add(new_resp)
        db.session.commit()

        # Analytics page should load successfully
        res = self.client.get("/teacher/imported-quiz/analytics?quiz=Unit-3+Assessment")
        self.assertEqual(res.status_code, 200)
        html = res.get_data(as_text=True)
        self.assertIn("USN_CHARLIE", html)
        self.assertIn("UNCALIBRATED", html)

        # CSV exports should also succeed without error
        res_csv = self.client.get("/teacher/imported-quiz/export/students?quiz=Unit-3+Assessment")
        self.assertEqual(res_csv.status_code, 200)
        csv_text = res_csv.get_data(as_text=True)
        self.assertIn("Not Estimated", csv_text)

    # -------------------------------------------------------------
    # 10. BKT Recalculation Idempotency Verification
    # -------------------------------------------------------------
    def test_bkt_recalculation_verification(self):
        """Verifies recalculate_models_only runs on complete history without duplicating records."""
        self._login_teacher()

        # Run recalculation over all imported responses
        res1 = AdaptivePipeline.recalculate_models_only()
        self.assertEqual(res1["status"], "SUCCESS")

        mastery_count_1 = StudentMastery.query.count()
        theta_count_1 = StudentTheta.query.count()
        cal_count_1 = ItemCalibration.query.count()

        # Total unique student-concept pairs in DB responses:
        # (Alice, Basics), (Alice, Hypothesis), (Bob, Basics), (Bob, Hypothesis), (Alice, Sampling) = 5
        self.assertEqual(mastery_count_1, 5)
        self.assertEqual(theta_count_1, 2)
        self.assertEqual(cal_count_1, 3)

        # Run recalculation a second time to verify idempotency (no duplicate rows created)
        res2 = AdaptivePipeline.recalculate_models_only()
        self.assertEqual(res2["status"], "SUCCESS")

        self.assertEqual(StudentMastery.query.count(), mastery_count_1)
        self.assertEqual(StudentTheta.query.count(), theta_count_1)
        self.assertEqual(ItemCalibration.query.count(), cal_count_1)


if __name__ == "__main__":
    unittest.main()
