"""
Comprehensive test suite for RAG, IRT+BKT Adaptive Engine, MCQ Validation,
Teacher Review Workflow, and LoRA Fine-Tuning Dataset Export.
"""

import io
import json
import unittest
from unittest import mock
from datetime import datetime

from pptx import Presentation

from app import create_app
from app.models import (
    db,
    Teacher,
    Student,
    CourseDocument,
    DocumentChunk,
    GeneratedQuestionRecord,
    Quiz,
    Question,
    StudentTheta,
    StudentMastery,
)
from app.rag.extractor import (
    extract_document,
    EmptyDocumentError,
    UnsupportedFormatError,
)
from app.rag.chunker import chunk_extracted_units
from app.rag.embeddings import embed_text, cosine_similarity
from app.rag.retriever import retrieve_context
from app.ai.adaptive_engine import AdaptiveEngine
from app.ai.validator import validate_mcq_data, parse_json_from_model_output, strip_thinking_tokens
from app.ai.generator import generate_adaptive_question
from app.ai.exporter import export_approved_dataset_jsonl


class TestRAGPipeline(unittest.TestCase):

    def setUp(self):
        self.app = create_app({
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "SECRET_KEY": "test-rag-secret",
            "WTF_CSRF_ENABLED": False,
        })
        self.client = self.app.test_client()
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()

        # Seed test teacher
        self.teacher = Teacher(
            teacher_id="T_RAG_01",
            first_name="Alan",
            last_name="Turing",
        )
        self.teacher.set_password("secret123")
        db.session.add(self.teacher)

        # Seed test student
        self.student = Student(
            usn="1RV21CS001",
            first_name="Ada",
            last_name="Lovelace",
            course="RM",
        )
        self.student.set_password("secret123")
        db.session.add(self.student)

        # Seed StudentTheta and StudentMastery
        self.theta = StudentTheta(usn="1RV21CS001", theta=1.25, quizzes_attempted=3)
        self.mastery = StudentMastery(usn="1RV21CS001", concept_tag="Sampling", mastery=0.35, attempts=4)
        db.session.add_all([self.theta, self.mastery])
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    # -----------------------------------------------------------------------
    # 1. Document Extraction Tests
    # -----------------------------------------------------------------------

    def test_extract_txt_content(self):
        txt_content = b"Research methodology involves systematic investigation and empirical hypothesis testing."
        units = extract_document(txt_content, "notes.txt")
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["page_number"], 1)
        self.assertIn("systematic investigation", units[0]["text"])

    def test_extract_pptx_content(self):
        # Create an in-memory PPTX
        prs = Presentation()
        slide_layout = prs.slide_layouts[0]
        slide = prs.slides.add_slide(slide_layout)
        title = slide.shapes.title
        subtitle = slide.placeholders[1]
        title.text = "Stratified Sampling Methods"
        subtitle.text = "Divide the population into homogeneous strata."

        ppt_io = io.BytesIO()
        prs.save(ppt_io)
        ppt_bytes = ppt_io.getvalue()

        units = extract_document(ppt_bytes, "lecture1.pptx")
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["slide_number"], 1)
        self.assertIn("Stratified Sampling", units[0]["text"])
        self.assertIn("homogeneous strata", units[0]["text"])

    def test_extract_empty_file_raises_error(self):
        with self.assertRaises(EmptyDocumentError):
            extract_document(b"", "empty.txt")

    def test_extract_unsupported_format_raises_error(self):
        with self.assertRaises(UnsupportedFormatError):
            extract_document(b"some audio bytes", "song.mp3")

    # -----------------------------------------------------------------------
    # 2. Chunking Tests
    # -----------------------------------------------------------------------

    def test_chunker_preserves_slide_numbers(self):
        units = [
            {"slide_number": 1, "text": "Slide 1: Overview of Qualitative Research and thematic analysis."},
            {"slide_number": 2, "text": "Slide 2: Quantitative Research relies on numerical measurements."},
        ]
        chunks = chunk_extracted_units(units, target_size=150, overlap=30)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["slide_number"], 1)
        self.assertIn("Qualitative Research", chunks[0]["content"])
        self.assertEqual(chunks[1]["slide_number"], 2)
        self.assertIn("Quantitative Research", chunks[1]["content"])
        self.assertGreater(chunks[0]["token_count"], 0)

    # -----------------------------------------------------------------------
    # 3. Embeddings & Semantic Retrieval Tests
    # -----------------------------------------------------------------------

    def test_embedding_engine_cosine_similarity(self):
        emb_sampling = embed_text("Stratified sampling divides population into homogeneous groups")
        emb_cluster = embed_text("Cluster sampling selects entire groups at random")
        emb_music = embed_text("Symphony orchestra violin performance classical music")

        self.assertEqual(len(emb_sampling), 256)
        sim_related = cosine_similarity(emb_sampling, emb_cluster)
        sim_unrelated = cosine_similarity(emb_sampling, emb_music)

        self.assertGreater(sim_related, sim_unrelated)

    def test_retriever_course_and_concept_filtering(self):
        # Create course documents for RM and CS
        doc_rm = CourseDocument(
            teacher_id=self.teacher.teacher_id,
            title="RM Slides",
            filename="rm_slides.pptx",
            file_type="pptx",
            course="RM",
            topic="Sampling",
            status="PROCESSED",
        )
        doc_cs = CourseDocument(
            teacher_id=self.teacher.teacher_id,
            title="CS Algo",
            filename="cs_algo.pdf",
            file_type="pdf",
            course="CS",
            topic="Sorting",
            status="PROCESSED",
        )
        db.session.add_all([doc_rm, doc_cs])
        db.session.commit()

        chunk_rm = DocumentChunk(
            document_id=doc_rm.id,
            chunk_index=0,
            course="RM",
            topic="Sampling",
            concept_tag="Sampling",
            content="Stratified sampling guarantees proportional representation of subgroups.",
            token_count=12,
            slide_number=5,
            embedding_json=json.dumps(embed_text("Stratified sampling subgroups")),
        )
        chunk_cs = DocumentChunk(
            document_id=doc_cs.id,
            chunk_index=0,
            course="CS",
            topic="Sorting",
            concept_tag="Sorting",
            content="Quicksort is a divide and conquer sorting algorithm.",
            token_count=11,
            page_number=12,
            embedding_json=json.dumps(embed_text("Quicksort divide and conquer")),
        )
        db.session.add_all([chunk_rm, chunk_cs])
        db.session.commit()

        # Retrieve for RM: course filter must strictly isolate RM
        result_rm = retrieve_context(
            course="RM",
            concept_tag="Sampling",
            query_text="Stratified sampling representation",
            top_k=2,
            similarity_threshold=0.1,
        )
        self.assertTrue(result_rm["found"])
        self.assertEqual(len(result_rm["chunks"]), 1)
        self.assertIn("Stratified sampling", result_rm["combined_context"])
        self.assertEqual(result_rm["chunks"][0]["slide_number"], 5)

        # Retrieval for nonexistent concept or course
        result_none = retrieve_context(
            course="PHYSICS",
            query_text="Quantum teleportation physics",
            top_k=2,
            similarity_threshold=0.1,
        )
        self.assertFalse(result_none["found"])

    # -----------------------------------------------------------------------
    # 4. Pedagogical Adaptive Engine Tests
    # -----------------------------------------------------------------------

    def test_adaptive_mapping_combinations(self):
        # High theta, High mastery -> Hard, Analyze
        p1 = AdaptiveEngine.determine_challenge_level(theta=2.2, mastery=0.85)
        self.assertEqual(p1["difficulty"], "Hard")
        self.assertEqual(p1["bloom_level"], "Analyze")

        # High theta, Low mastery -> Medium, Understand
        p2 = AdaptiveEngine.determine_challenge_level(theta=1.8, mastery=0.25)
        self.assertEqual(p2["difficulty"], "Medium")
        self.assertEqual(p2["bloom_level"], "Understand")

        # Low theta, High mastery -> Medium, Understand
        p3 = AdaptiveEngine.determine_challenge_level(theta=-0.5, mastery=0.80)
        self.assertEqual(p3["difficulty"], "Medium")
        self.assertEqual(p3["bloom_level"], "Understand")

        # Low theta, Low mastery -> Easy, Remember
        p4 = AdaptiveEngine.determine_challenge_level(theta=-2.0, mastery=0.20)
        self.assertEqual(p4["difficulty"], "Easy")
        self.assertEqual(p4["bloom_level"], "Remember")

        # Missing values (None) defaults safely to priors
        p5 = AdaptiveEngine.determine_challenge_level(theta=None, mastery=None)
        self.assertEqual(p5["difficulty"], "Easy")
        self.assertEqual(p5["bloom_level"], "Remember")

    # -----------------------------------------------------------------------
    # 5. Output Validation Tests
    # -----------------------------------------------------------------------

    def test_strip_thinking_tokens(self):
        raw = "<think>Let me think about research methods.</think>{\"question\": \"What is sampling?\"}"
        cleaned = strip_thinking_tokens(raw)
        self.assertEqual(cleaned, "{\"question\": \"What is sampling?\"}")

    def test_validate_mcq_data_success(self):
        context = "Simple random sampling gives every member an equal probability of selection."
        valid_dict = {
            "question": "In simple random sampling, what probability does each member have?",
            "option_a": "An equal probability of selection",
            "option_b": "A zero probability of selection",
            "option_c": "A variable unknown probability",
            "option_d": "A proportional stratified probability",
            "correct_option": "A",
            "explanation": "Simple random sampling ensures all members have an equal probability of being selected."
        }

        is_valid, errors, normalized = validate_mcq_data(valid_dict, context)
        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(normalized["correct_option"], "A")

    def test_validate_mcq_duplicate_options_rejected(self):
        context = "Sampling techniques are important."
        invalid_dict = {
            "question": "What is sampling?",
            "option_a": "Duplicate option",
            "option_b": "Duplicate option",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_option": "A",
            "explanation": "Because it is duplicate."
        }
        is_valid, errors, normalized = validate_mcq_data(invalid_dict, context)
        self.assertFalse(is_valid)
        self.assertTrue(any("duplicate" in e.lower() for e in errors))

    def test_validate_mcq_invalid_key_rejected(self):
        context = "Sampling techniques are important."
        invalid_dict = {
            "question": "What is sampling?",
            "option_a": "Option A",
            "option_b": "Option B",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_option": "E",
            "explanation": "Option E is not valid."
        }
        is_valid, errors, normalized = validate_mcq_data(invalid_dict, context)
        self.assertFalse(is_valid)
        self.assertTrue(any("not one of 'a', 'b', 'c', 'd'" in e.lower() for e in errors))

    # -----------------------------------------------------------------------
    # 6. Full Question Generation & Record Persistence
    # -----------------------------------------------------------------------

    @mock.patch("app.ai.generator.QwenLoRAModelService.get_instance")
    def test_generate_adaptive_question_persists_record(self, mock_get_service):
        mock_service = mock.MagicMock()
        mock_service.is_loaded.return_value = True
        mock_service.base_model_name = "Qwen/Qwen3-0.6B"
        mock_service.adapter_path = "models/fine_tuned_qwen_lora"
        mock_service.generate.return_value = json.dumps({
            "question": "What is stratified sampling based on course materials?",
            "option_a": "Divides a heterogeneous population into homogeneous subgroups called strata",
            "option_b": "A technique without data collection",
            "option_c": "An arbitrary guess",
            "option_d": "A non-probabilistic shortcut",
            "correct_option": "A",
            "explanation": "Stratified sampling ensures representation of each stratum."
        })
        mock_get_service.return_value = mock_service

        # Ingest a real sample document first
        doc = CourseDocument(
            teacher_id=self.teacher.teacher_id,
            title="Research Unit 1",
            filename="research_unit1.txt",
            file_type="txt",
            course="RM",
            topic="Sampling",
            status="PROCESSED",
        )
        db.session.add(doc)
        db.session.commit()

        chunk = DocumentChunk(
            document_id=doc.id,
            chunk_index=0,
            course="RM",
            topic="Sampling",
            concept_tag="Sampling",
            content="Stratified sampling divides a heterogeneous population into homogeneous subgroups called strata.",
            token_count=16,
            slide_number=3,
            embedding_json=json.dumps(embed_text("Stratified sampling heterogeneous population strata")),
        )
        db.session.add(chunk)
        db.session.commit()

        # Generate question for student Ada
        res = generate_adaptive_question(
            course="RM",
            concept_tag="Sampling",
            usn="1RV21CS001",
        )

        self.assertIn("record_id", res)
        self.assertIn("question", res)
        self.assertIn("options", res)
        self.assertIn("correct_option", res)
        self.assertIn("source_ref", res)
        self.assertIn("Slide 3", res["source_ref"])

        # Verify DB record
        record = GeneratedQuestionRecord.query.get(res["record_id"])
        self.assertIsNotNone(record)
        self.assertIn("Slide 3", record.source_page_or_slide)
        self.assertEqual(record.course, "RM")
        self.assertEqual(record.concept_tag, "Sampling")
        self.assertAlmostEqual(record.learner_theta, 1.25, places=2)
        self.assertAlmostEqual(record.learner_mastery, 0.35, places=2)
        self.assertEqual(record.validation_status, "pending_review")

    # -----------------------------------------------------------------------
    # 7. Teacher Review Workflow Tests
    # -----------------------------------------------------------------------

    def test_teacher_approve_reject_and_edit_workflow(self):
        record = GeneratedQuestionRecord(
            course="RM",
            concept_tag="Sampling",
            target_difficulty="Medium",
            target_bloom="Apply",
            question_text="What is stratified sampling?",
            option_a="Divides population into strata",
            option_b="Random lottery",
            option_c="Voluntary response",
            option_d="Haphazard guessing",
            correct_option="A",
            explanation="Strata are homogeneous groups.",
            source_context="Stratified sampling divides population into homogeneous strata.",
            source_filename="rm_slides.pptx",
            source_page_or_slide="Slide 5",
            validation_status="pending_review",
        )
        db.session.add(record)
        db.session.commit()

        # Log in as teacher
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.teacher.teacher_id
            sess["role"] = "teacher"

        # 1. Edit & Curate
        edit_resp = self.client.post(
            f"/teacher/generated-questions/{record.id}/edit",
            data={
                "question_text": "How does stratified random sampling ensure subgroup representation?",
                "option_a": "By partitioning the population into homogeneous strata before sampling",
                "option_b": "By picking subjects purely at convenience",
                "option_c": "By sampling only extreme outliers",
                "option_d": "By surveying everyone without sampling",
                "correct_option": "A",
                "explanation": "Homogeneous strata ensure all subgroups are represented proportionally.",
                "target_difficulty": "Hard",
                "target_bloom": "Analyze",
                "teacher_notes": "Refined distractor quality for SFT dataset.",
            },
            follow_redirects=True,
        )
        self.assertEqual(edit_resp.status_code, 200)

        # Verify edited record
        db.session.refresh(record)
        self.assertTrue(record.teacher_edited)
        self.assertEqual(record.validation_status, "approved")
        self.assertEqual(record.target_difficulty, "Hard")
        self.assertIn("stratified random sampling", record.question_text)
        self.assertIn("homogeneous strata", record.option_a)

        # 2. Add to classical quiz
        quiz = Quiz(
            title="Unit 1 Classical Quiz",
            course="RM",
            teacher_id=self.teacher.teacher_id,
        )
        db.session.add(quiz)
        db.session.commit()

        add_resp = self.client.post(
            f"/teacher/generated-questions/{record.id}/add-to-quiz",
            data={"quiz_id": quiz.id},
            follow_redirects=True,
        )
        self.assertEqual(add_resp.status_code, 200)

        quiz_q = Question.query.filter_by(quiz_id=quiz.id).first()
        self.assertIsNotNone(quiz_q)
        self.assertEqual(quiz_q.correct_option, "A")
        self.assertIn("stratified random sampling", quiz_q.question_text)

        # 3. Reject endpoint test on a second record
        bad_record = GeneratedQuestionRecord(
            course="RM",
            concept_tag="Sampling",
            target_difficulty="Easy",
            target_bloom="Remember",
            question_text="Hallucinated question?",
            option_a="A",
            option_b="B",
            option_c="C",
            option_d="D",
            correct_option="A",
            explanation="None",
            source_context="Some context",
            source_filename="slides.pptx",
            source_page_or_slide="Slide 1",
            validation_status="pending_review",
        )
        db.session.add(bad_record)
        db.session.commit()

        rej_resp = self.client.post(
            f"/teacher/generated-questions/{bad_record.id}/reject",
            data={"reason": "Contains inaccurate fact"},
            follow_redirects=True,
        )
        self.assertEqual(rej_resp.status_code, 200)
        db.session.refresh(bad_record)
        self.assertEqual(bad_record.validation_status, "rejected")
        self.assertEqual(bad_record.teacher_notes, "Contains inaccurate fact")

    # -----------------------------------------------------------------------
    # 8. LoRA Fine-Tuning Dataset Export Tests (No PII, Approved Only)
    # -----------------------------------------------------------------------

    def test_lora_dataset_export_zero_pii_and_approved_only(self):
        # Record 1: Approved
        r1 = GeneratedQuestionRecord(
            course="RM",
            concept_tag="Sampling",
            target_difficulty="Medium",
            target_bloom="Apply",
            question_text="Approved question 1?",
            option_a="Correct option",
            option_b="Distractor 1",
            option_c="Distractor 2",
            option_d="Distractor 3",
            correct_option="A",
            explanation="Grounded explanation",
            source_context="Course material slide text about sampling.",
            source_filename="rm_slides.pptx",
            source_page_or_slide="Slide 2",
            learner_theta=1.2,
            learner_mastery=0.45,
            validation_status="approved",
        )
        # Record 2: Pending review (must be excluded from export!)
        r2 = GeneratedQuestionRecord(
            course="RM",
            concept_tag="Sampling",
            target_difficulty="Easy",
            target_bloom="Remember",
            question_text="Pending question 2?",
            option_a="A",
            option_b="B",
            option_c="C",
            option_d="D",
            correct_option="B",
            explanation="Pending explanation",
            source_context="Context 2",
            validation_status="pending_review",
        )
        # Record 3: Rejected (must be excluded from export!)
        r3 = GeneratedQuestionRecord(
            course="RM",
            concept_tag="Sampling",
            target_difficulty="Hard",
            target_bloom="Analyze",
            question_text="Rejected question 3?",
            option_a="A",
            option_b="B",
            option_c="C",
            option_d="D",
            correct_option="C",
            explanation="Rejected explanation",
            source_context="Context 3",
            validation_status="rejected",
        )
        db.session.add_all([r1, r2, r3])
        db.session.commit()

        # Log in as teacher
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.teacher.teacher_id
            sess["role"] = "teacher"

        response = self.client.get("/teacher/export/lora-dataset.jsonl")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/jsonlines", response.headers["Content-Type"])

        lines = [line.strip() for line in response.data.decode("utf-8").split("\n") if line.strip()]
        # Only Record 1 should be exported!
        self.assertEqual(len(lines), 1)

        data = json.loads(lines[0])
        self.assertIn("instruction", data)
        self.assertIn("input", data)
        self.assertIn("output", data)
        self.assertIn("metadata", data)

        # STRICT PRIVACY & PII CHECKS:
        raw_text = response.data.decode("utf-8")
        self.assertNotIn("1RV21CS001", raw_text, "Student USN must NEVER appear in LoRA dataset export!")
        self.assertNotIn("Ada", raw_text, "Student name must NEVER appear in LoRA dataset export!")
        self.assertNotIn("Lovelace", raw_text)

        # Format verification
        out_obj = data["output"]
        self.assertEqual(out_obj["correct_answer"], "A")
        self.assertEqual(out_obj["options"]["A"], "Correct option")
        self.assertEqual(data["input"]["difficulty"], "Medium")
        self.assertEqual(data["input"]["bloom_level"], "Apply")


if __name__ == "__main__":
    unittest.main()
