"""
Tests for CSV schema validation, parsing, normalization, and timestamps.
"""

import io
import unittest
from datetime import datetime
import pandas as pd

from app.ingestion.csv_reader import read_weekly_csv, compute_file_hash, REQUIRED_COLUMNS
from app.ingestion.processor import (
    normalize_option,
    parse_timestamp,
    normalize_response,
    build_question_catalog,
    process_weekly_csv,
)


class TestCSVValidation(unittest.TestCase):

    def setUp(self):
        self.sample_valid_csv = """Rank,Attempt,Student Name,Student ID / USN,Quiz,Subject,Question No,Question ID,Concept Tag,Bloom's Level,Question,Option A,Option B,Option C,Option D,Selected Option,Correct Option,Question Marks,Marks Obtained,Total Marks,Percentage,Correct Answers,Wrong Answers,Unanswered,Violation,Violation Reason,Submitted At
1,1,Alice Smith,USN001,Unit-1 Quiz,RM,1,QID1,Types of Research,Remember,What is research?,A,B,C,D,A,A,1,1,20,5.0%,1,0,0,NO,,02/09/2026 8:40:12 AM
"""

    def test_missing_column_error(self):
        # CSV missing 'Question ID' and 'Attempt'
        invalid_csv = """Student Name,Student ID / USN,Quiz,Subject,Question No,Question,Selected Option,Correct Option,Question Marks,Marks Obtained,Submitted At
Alice,USN001,Quiz1,RM,1,Q?,A,A,1,1,02/09/2026 8:40:12 AM
"""
        with self.assertRaises(ValueError) as ctx:
            read_weekly_csv(io.StringIO(invalid_csv))
        self.assertIn("missing", str(ctx.exception).lower())
        self.assertIn("Question ID", str(ctx.exception))

    def test_empty_csv_error(self):
        empty_csv = ""
        with self.assertRaises(ValueError):
            read_weekly_csv(io.StringIO(empty_csv))

    def test_file_hash_consistency(self):
        content = b"sample,csv,data"
        h1 = compute_file_hash(io.BytesIO(content))
        h2 = compute_file_hash(io.BytesIO(content))
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_normalize_option(self):
        self.assertEqual(normalize_option("A"), "A")
        self.assertEqual(normalize_option(" b "), "B")
        self.assertIsNone(normalize_option("Not Answered"))
        self.assertIsNone(normalize_option("none"))
        self.assertIsNone(normalize_option(""))
        self.assertIsNone(normalize_option(None))
        self.assertIsNone(normalize_option(float("nan")))

    def test_parse_timestamp_formats(self):
        # 12-hour AM/PM format
        t1 = parse_timestamp("02/09/2026 8:40:12 AM")
        self.assertIsNotNone(t1)
        self.assertEqual(t1.day, 2)
        self.assertEqual(t1.month, 9)
        self.assertEqual(t1.year, 2026)
        self.assertEqual(t1.hour, 8)
        self.assertEqual(t1.minute, 40)
        self.assertEqual(t1.second, 12)

        # 24-hour format
        t2 = parse_timestamp("02-09-2026 14:30")
        self.assertIsNotNone(t2)
        self.assertEqual(t2.hour, 14)
        self.assertEqual(t2.minute, 30)

        # Invalid format
        self.assertIsNone(parse_timestamp("invalid-date-string"))
        self.assertIsNone(parse_timestamp(None))

    def test_correct_and_incorrect_response_derivation(self):
        row_correct = {
            "Student ID / USN": "USN01",
            "Student Name": "John Doe",
            "Quiz": "QZ1",
            "Subject": "RM",
            "Attempt": 1,
            "Question No": 1,
            "Question ID": "Q1",
            "Concept Tag": "Tag A",
            "Bloom's Level": "Apply",
            "Selected Option": "A",
            "Correct Option": "A",
            "Marks Obtained": 1,
            "Question Marks": 1,
            "Submitted At": "02/09/2026 8:40:12 AM",
        }
        rec, warn = normalize_response(row_correct, row_index=1)
        self.assertTrue(rec["is_correct"])
        self.assertFalse(rec["is_unanswered"])
        self.assertEqual(rec["selected_option"], "A")
        self.assertIsNone(warn)

        row_incorrect = dict(row_correct, **{"Selected Option": "B"})
        rec, warn = normalize_response(row_incorrect, row_index=2)
        self.assertFalse(rec["is_correct"])
        self.assertFalse(rec["is_unanswered"])
        self.assertEqual(rec["selected_option"], "B")

    def test_unanswered_response_handling(self):
        row_unanswered = {
            "Student ID / USN": "USN01",
            "Student Name": "John Doe",
            "Quiz": "QZ1",
            "Subject": "RM",
            "Attempt": 1,
            "Question No": 1,
            "Question ID": "Q1",
            "Concept Tag": "Tag A",
            "Bloom's Level": "Apply",
            "Selected Option": "Not Answered",
            "Correct Option": "A",
            "Marks Obtained": 0,
            "Question Marks": 1,
            "Submitted At": "02/09/2026 8:40:12 AM",
        }
        rec, warn = normalize_response(row_unanswered, row_index=1)
        self.assertFalse(rec["is_correct"])
        self.assertTrue(rec["is_unanswered"])
        self.assertIsNone(rec["selected_option"])
        self.assertIsNone(warn)

    def test_inconsistency_logging(self):
        # Student chose A (matches Correct A), but CSV says Marks Obtained = 0
        row_inconsistent = {
            "Student ID / USN": "USN01",
            "Student Name": "John Doe",
            "Quiz": "QZ1",
            "Subject": "RM",
            "Attempt": 1,
            "Question No": 1,
            "Question ID": "Q1",
            "Concept Tag": "Tag A",
            "Bloom's Level": "Apply",
            "Selected Option": "A",
            "Correct Option": "A",
            "Marks Obtained": 0,
            "Question Marks": 1,
            "Submitted At": "02/09/2026 8:40:12 AM",
        }
        rec, warn = normalize_response(row_inconsistent, row_index=1)
        self.assertTrue(rec["is_correct"])
        self.assertIsNotNone(warn)
        self.assertIn("inconsistency", warn.lower())

    def test_question_catalog_backfill(self):
        # Simulate rows where some have Bloom's Level and options missing
        csv_data = """Rank,Attempt,Student Name,Student ID / USN,Quiz,Subject,Question No,Question ID,Concept Tag,Bloom's Level,Question,Option A,Option B,Option C,Option D,Selected Option,Correct Option,Question Marks,Marks Obtained,Total Marks,Percentage,Correct Answers,Wrong Answers,Unanswered,Violation,Violation Reason,Submitted At
1,1,Alice,USN1,Quiz1,RM,1,QID100,Research Basics,Remember,Question 100?,OptA,OptB,OptC,OptD,A,A,1,1,20,5.0%,1,0,0,NO,,02/09/2026 8:40:12 AM
2,2,Bob,USN2,Quiz1,RM,1,QID100,Research Basics,,Question 100?,,,,,B,A,1,0,20,0.0%,0,1,0,NO,,02/09/2026 8:45:12 AM
"""
        res = process_weekly_csv(io.StringIO(csv_data))
        self.assertEqual(len(res["records"]), 2)
        # Second row should have Bloom's level backfilled to 'Remember' and options backfilled
        self.assertEqual(res["records"][1]["bloom_level"], "Remember")
        self.assertEqual(res["records"][1]["option_a"], "OptA")


if __name__ == "__main__":
    unittest.main()
