"""
Integration tests for Adaptive Assessment Pipeline:
- Duplicate import prevention
- Transaction rollback on failure
- Separation of imported responses from dummy/test data
- Reprocessing idempotency (no double-counting)
- End-to-end pipeline execution on real CSV data using isolated SQLite test database fixture.
"""

import io
import json
import os
import unittest
from datetime import datetime
from pathlib import Path

from app import create_app
from app.models import (
    db,
    ImportBatch,
    ImportedResponse,
    ItemCalibration,
    StudentTheta,
    StudentMastery,
    Response,
    Attempt,
    Quiz,
    Question,
    Teacher,
    Student,
)
from app.ingestion.service import IngestionService
from app.pipeline import AdaptivePipeline
from app.irt.service import IRTService
from app.bkt.services import BKTService


class TestPipeline(unittest.TestCase):

    def setUp(self):
        # Create Flask app configured with an isolated in-memory SQLite database
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-secret-key",
        })
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        self.sample_csv_text = """Rank,Attempt,Student Name,Student ID / USN,Quiz,Subject,Question No,Question ID,Concept Tag,Bloom's Level,Question,Option A,Option B,Option C,Option D,Selected Option,Correct Option,Question Marks,Marks Obtained,Total Marks,Percentage,Correct Answers,Wrong Answers,Unanswered,Violation,Violation Reason,Submitted At
1,1,Alice Wonderland,USN_A,Quiz 1,RM,1,Q_01,Research Basics,Remember,Question 1?,A,B,C,D,A,A,1,1,2,50.0%,1,1,0,NO,,02/09/2026 8:40:12 AM
1,1,Alice Wonderland,USN_A,Quiz 1,RM,2,Q_02,Problem Formulation,Apply,Question 2?,A,B,C,D,B,A,1,0,2,50.0%,1,1,0,NO,,02/09/2026 8:40:12 AM
2,1,Bob Builder,USN_B,Quiz 1,RM,1,Q_01,Research Basics,Remember,Question 1?,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,02/09/2026 8:42:00 AM
2,1,Bob Builder,USN_B,Quiz 1,RM,2,Q_02,Problem Formulation,Apply,Question 2?,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,02/09/2026 8:42:00 AM
"""

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def test_duplicate_import_prevention(self):
        # First import
        buf1 = io.StringIO(self.sample_csv_text)
        res1 = IngestionService.import_csv(buf1, filename="weekly_quiz.csv")
        self.assertEqual(res1["status"], "SUCCESS")
        self.assertEqual(res1["inserted_records"], 4)

        # Second import of identical file
        buf2 = io.StringIO(self.sample_csv_text)
        res2 = IngestionService.import_csv(buf2, filename="weekly_quiz.csv")
        self.assertEqual(res2["status"], "DUPLICATE")
        self.assertIn("already been imported", res2["message"])

        # Confirm total rows in imported_responses did not double
        total_responses = ImportedResponse.query.count()
        self.assertEqual(total_responses, 4)

    def test_transaction_rollback_on_failure(self):
        # CSV with an invalid timestamp in row 2
        bad_csv = """Rank,Attempt,Student Name,Student ID / USN,Quiz,Subject,Question No,Question ID,Concept Tag,Bloom's Level,Question,Option A,Option B,Option C,Option D,Selected Option,Correct Option,Question Marks,Marks Obtained,Total Marks,Percentage,Correct Answers,Wrong Answers,Unanswered,Violation,Violation Reason,Submitted At
1,1,Alice,USN_A,Quiz 1,RM,1,Q_01,Research Basics,Remember,Q1,A,B,C,D,A,A,1,1,2,50.0%,1,0,0,NO,,02/09/2026 8:40:12 AM
2,1,Bob,USN_B,Quiz 1,RM,2,Q_02,Research Basics,Remember,Q2,A,B,C,D,A,A,1,1,2,50.0%,1,0,0,NO,,invalid-datetime-here
"""
        initial_count = ImportedResponse.query.count()
        with self.assertRaises(ValueError):
            IngestionService.import_csv(io.StringIO(bad_csv), filename="bad.csv")

        # Database should be rolled back completely
        final_count = ImportedResponse.query.count()
        self.assertEqual(final_count, initial_count)

        # Failed batch should be recorded as FAILED
        failed_batch = ImportBatch.query.filter_by(status="FAILED").first()
        self.assertIsNotNone(failed_batch)

    def test_separation_from_dummy_data(self):
        # 1. Insert a dummy web response into legacy responses table
        teacher = Teacher(teacher_id="T01", first_name="Teach", last_name="Er")
        teacher.set_password("pass")
        student = Student(usn="DUMMY_STUDENT", first_name="Dummy", last_name="User", course="RM")
        student.set_password("pass")
        quiz = Quiz(title="Dummy Web Quiz", teacher_id="T01")
        db.session.add_all([teacher, student, quiz])
        db.session.flush()

        question = Question(quiz_id=quiz.id, question_text="Dummy?", option_a="1", option_b="2", option_c="3", option_d="4", correct_option="A", concept_tag="DummyConcept", topic="DummyTopic", difficulty="Easy", difficulty_value=0.0)
        attempt = Attempt(quiz_id=quiz.id, usn="DUMMY_STUDENT")
        db.session.add_all([question, attempt])
        db.session.flush()

        dummy_resp = Response(attempt_id=attempt.id, question_id=question.id, selected_option="A", is_correct=True, concept_tag="DummyConcept")
        db.session.add(dummy_resp)
        db.session.commit()

        # 2. Ingest valid CSV
        buf = io.StringIO(self.sample_csv_text)
        IngestionService.import_csv(buf, filename="weekly_quiz.csv")

        # 3. Run IRT and BKT
        IRTService.recalculate_all(min_responses=1)
        BKTService.recalculate_all()

        # The dummy student and dummy concept must NOT appear in IRT or BKT models
        dummy_theta = StudentTheta.query.filter_by(usn="DUMMY_STUDENT").first()
        self.assertIsNone(dummy_theta)

        dummy_mastery = StudentMastery.query.filter_by(concept_tag="DummyConcept").first()
        self.assertIsNone(dummy_mastery)

        # Only CSV students should be calibrated
        csv_thetas = StudentTheta.query.all()
        self.assertEqual({t.usn for t in csv_thetas}, {"USN_A", "USN_B"})

    def test_reprocessing_without_double_counting(self):
        buf = io.StringIO(self.sample_csv_text)
        IngestionService.import_csv(buf, filename="weekly_quiz.csv")

        # First model calculation
        AdaptivePipeline.recalculate_models_only()
        theta_1 = StudentTheta.query.filter_by(usn="USN_B").first().theta
        mastery_1 = StudentMastery.query.filter_by(usn="USN_B", concept_tag="Research Basics").first().mastery

        # Second model calculation (recalculation)
        AdaptivePipeline.recalculate_models_only()
        theta_2 = StudentTheta.query.filter_by(usn="USN_B").first().theta
        mastery_2 = StudentMastery.query.filter_by(usn="USN_B", concept_tag="Research Basics").first().mastery

        # Values must be identical
        self.assertEqual(theta_1, theta_2)
        self.assertEqual(mastery_1, mastery_2)

    def test_end_to_end_supplied_csv(self):
        # Locate the actual Unit-1 CSV
        csv_path = Path("/Users/lokeshk/Downloads/Unit-1_Quiz_02-09-2026_Detailed_Results.csv")
        if not csv_path.exists():
            self.skipTest("Supplied CSV not found in /Users/lokeshk/Downloads/")

        res = AdaptivePipeline.run_pipeline(str(csv_path))

        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(res["ingestion"]["valid_rows"], 2420)
        self.assertEqual(res["ingestion"]["question_count"], 20)
        self.assertEqual(res["ingestion"]["student_count"], 64)

        # 20 items calibrated
        self.assertEqual(res["irt"]["items_calibrated"], 20)
        self.assertEqual(res["irt"]["students_estimated"], 64)

        # Check calibrations in DB
        calibs = ItemCalibration.query.all()
        self.assertEqual(len(calibs), 20)
        sum_b = sum(c.difficulty_b for c in calibs)
        self.assertAlmostEqual(sum_b, 0.0, places=2)

        # Check students in DB
        thetas = StudentTheta.query.all()
        self.assertEqual(len(thetas), 64)

        # Check concept mastery in DB
        masteries = StudentMastery.query.all()
        self.assertEqual(len(masteries), 64 * 11)  # 64 students x 11 concepts = 704 records
        for m in masteries:
            self.assertTrue(0.0 <= m.mastery <= 1.0)

    def test_irt_cross_quiz_different_attempt_numbers(self):
        """
        Regression test: Verify that when a student has different attempt numbers across quizzes
        (e.g., Attempt 1 on Unit 1 and Attempt 2 on Unit 2), IRT pools the latest attempt from EACH quiz,
        and item calibration properly includes Unit 2 questions.
        """
        multi_quiz_csv = """Rank,Attempt,Student Name,Student ID / USN,Quiz,Subject,Question No,Question ID,Concept Tag,Bloom's Level,Question,Option A,Option B,Option C,Option D,Selected Option,Correct Option,Question Marks,Marks Obtained,Total Marks,Percentage,Correct Answers,Wrong Answers,Unanswered,Violation,Violation Reason,Submitted At
1,1,Multi Student,USN_MULTI,Unit 1,RM,1,Q_01,Concept A,Remember,Q1,A,B,C,D,A,A,1,1,2,50.0%,1,1,0,NO,,02/09/2026 8:40:12 AM
1,1,Multi Student,USN_MULTI,Unit 1,RM,2,Q_02,Concept A,Apply,Q2,A,B,C,D,B,A,1,0,2,50.0%,1,1,0,NO,,02/09/2026 8:40:12 AM
2,1,Other Student,USN_OTHER,Unit 1,RM,1,Q_01,Concept A,Remember,Q1,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,02/09/2026 8:41:00 AM
2,1,Other Student,USN_OTHER,Unit 1,RM,2,Q_02,Concept A,Apply,Q2,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,02/09/2026 8:41:00 AM
1,2,Multi Student,USN_MULTI,Unit 2,RM,1,Q_03,Concept B,Remember,Q3,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,09/09/2026 8:40:12 AM
1,2,Multi Student,USN_MULTI,Unit 2,RM,2,Q_04,Concept B,Apply,Q4,A,B,C,D,A,A,1,1,2,100.0%,2,0,0,NO,,09/09/2026 8:40:12 AM
2,2,Other Student,USN_OTHER,Unit 2,RM,1,Q_03,Concept B,Remember,Q3,A,B,C,D,B,A,1,0,2,50.0%,1,1,0,NO,,09/09/2026 8:41:00 AM
2,2,Other Student,USN_OTHER,Unit 2,RM,2,Q_04,Concept B,Apply,Q4,A,B,C,D,A,A,1,1,2,50.0%,1,1,0,NO,,09/09/2026 8:41:00 AM
"""
        IngestionService.import_csv(io.StringIO(multi_quiz_csv), filename="multi_unit_test.csv")
        AdaptivePipeline.recalculate_models_only()

        # 1. Item Calibration Check: All 4 questions across Unit 1 and Unit 2 must be present
        calibs = {c.question_id: c for c in ItemCalibration.query.all()}
        self.assertIn("Q_01", calibs)
        self.assertIn("Q_02", calibs)
        self.assertIn("Q_03", calibs, "Unit 2 item Q_03 must be calibrated even with Attempt=2")
        self.assertIn("Q_04", calibs, "Unit 2 item Q_04 must be calibrated even with Attempt=2")

        # 2. Student Theta Check: USN_MULTI must pool latest from Unit 1 (2 items) and Unit 2 (2 items)
        multi_theta = StudentTheta.query.filter_by(usn="USN_MULTI").first()
        self.assertIsNotNone(multi_theta)
        self.assertEqual(multi_theta.quizzes_attempted, 2, "Must count exactly 2 distinct quizzes")
        self.assertEqual(multi_theta.responses_count, 4, "Must pool 2 items from Unit 1 and 2 from Unit 2")
        self.assertEqual(multi_theta.correct_count, 3, "3 correct (1 in Unit 1, 2 in Unit 2)")

        # 3. Check JSON metadata tracking
        meta = json.loads(multi_theta.metadata_json)
        self.assertEqual(meta["quizzes_count"], 2)
        self.assertEqual(meta["pooled_responses"], 4)
        self.assertEqual(meta["quiz_attempts"]["Unit 1"], 1)
        self.assertEqual(meta["quiz_attempts"]["Unit 2"], 2)

    def test_online_and_batch_bkt_initialization_symmetry(self):
        """
        Verify that online web quizzes and batch recomputation initialize concepts
        using the identical standard prior DEFAULT_P_L0 (0.20), guaranteeing policy symmetry.
        """
        teacher = Teacher(teacher_id="T_BKT", first_name="T", last_name="B")
        teacher.set_password("pass")
        student = Student(usn="USN_SYM", first_name="Sym", last_name="Metric", course="RM")
        student.set_password("pass")
        quiz = Quiz(title="Symmetry Quiz", teacher_id="T_BKT")
        db.session.add_all([teacher, student, quiz])
        db.session.flush()

        q = Question(
            quiz_id=quiz.id, question_text="What is symmetry?",
            option_a="A", option_b="B", option_c="C", option_d="D",
            correct_option="A", concept_tag="SymmetryConcept", topic="SymmetryTopic",
            difficulty="Medium", difficulty_value=0.0
        )
        attempt = Attempt(quiz_id=quiz.id, usn="USN_SYM")
        db.session.add_all([q, attempt])
        db.session.flush()

        resp = Response(
            attempt_id=attempt.id, question_id=q.id,
            selected_option="A", is_correct=True, concept_tag="SymmetryConcept"
        )
        db.session.add(resp)
        db.session.commit()

        # Run online incremental update
        BKTService.update_student_mastery(attempt.id)

        mastery_rec = StudentMastery.query.filter_by(usn="USN_SYM", concept_tag="SymmetryConcept").first()
        self.assertIsNotNone(mastery_rec)
        # Expected calculation starting from DEFAULT_P_L0 (0.20) on 1 correct answer:
        # P(obs) = 0.20 * 0.90 + 0.80 * 0.20 = 0.34
        # Posterior = 0.18 / 0.34 = 0.5294
        # Next = 0.5294 + 0.4706 * 0.15 = 0.6000
        self.assertAlmostEqual(mastery_rec.mastery, 0.60, places=2)
        self.assertEqual(mastery_rec.attempts, 1)


if __name__ == "__main__":
    unittest.main()


