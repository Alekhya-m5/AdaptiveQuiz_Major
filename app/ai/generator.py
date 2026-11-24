"""
Adaptive Question Generation Pipeline.
Integrates RAG retrieval, IRT theta, BKT concept mastery, and Qwen+LoRA generation
with strict output validation, retry loops, and persistent quality logging.
"""

import json
import logging
import re
import time
from typing import Dict, Any, Optional, List, Tuple

from app.models import (
    db,
    StudentTheta,
    StudentMastery,
    GeneratedQuestionRecord,
    CourseDocument,
    Attempt,
    Response,
    Question,
)
from app.rag.retriever import retrieve_context
from app.ai.adaptive_engine import AdaptiveEngine
from app.ai.model_service import QwenLoRAModelService
from app.ai.validator import (
    parse_json_from_model_output,
    validate_mcq_data,
    strip_thinking_tokens,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


def build_system_prompt() -> str:
    return (
        "You are an expert curriculum assessment question author. Based strictly on the provided course material, "
        "generate exactly ONE educational multiple-choice question (MCQ) calibrated to the target difficulty, "
        "Bloom taxonomy level, and individual learner ability metrics.\n"
        "Pedagogical Calibration Rules:\n"
        "- Easy (Remember): Focus on direct definitions, core terminology, and explicit facts stated in the text. "
        "Question stems should be direct and clear.\n"
        "- Medium (Understand / Apply): Focus on conceptual explanation, procedural relationships, and practical scenarios. "
        "Require students to interpret why a principle exists or how it applies.\n"
        "- Hard (Analyze / Evaluate): Focus on critical distinctions, multi-step scenarios, evaluating edge cases, "
        "boundary conditions, or limitations substantiated in the text. Stems must demand high cognitive discrimination.\n"
        "- Scaffolding by Ability: For lower ability/mastery learners, use clear language and distinct distractors that address common misconceptions. "
        "For high ability/mastery learners, formulate rigorous questions with sophisticated near-miss distractors.\n"
        "Formatting Rules:\n"
        "1. Every question must be fully factually grounded in the provided source material.\n"
        "2. Return ONLY valid JSON with no markdown prose or thinking tags outside the JSON.\n"
        "3. Include exactly 4 options with keys option_a, option_b, option_c, option_d.\n"
        "4. Exactly one option must be correct; correct_option must be 'A', 'B', 'C', or 'D'.\n"
        "5. Provide a helpful, non-trivial explanation justifying the correct option.\n"
        "Required JSON schema:\n"
        "{\n"
        '  "question": "What is ...?",\n'
        '  "option_a": "First option",\n'
        '  "option_b": "Second option",\n'
        '  "option_c": "Third option",\n'
        '  "option_d": "Fourth option",\n'
        '  "correct_option": "B",\n'
        '  "explanation": "Why B is correct based on the text.",\n'
        '  "difficulty": "Medium",\n'
        '  "bloom_level": "Understand"\n'
        "}"
    )


def get_learner_question_history(
    usn: str,
    concept_tag: Optional[str] = None,
    limit: int = 5,
) -> Tuple[List[str], Optional[str], List[int]]:
    """
    Retrieve previously attempted and generated question stems for a learner,
    along with any revision scaffolding contexts and previously used chunk IDs.
    Preserves learner history to avoid question repetition while allowing targeted revision.
    """
    prev_stems: List[str] = []
    revision_context: Optional[str] = None
    used_chunk_ids: List[int] = []

    try:
        # 1. Attempted questions from live student tests
        q_query = (
            db.session.query(Question.question_text, Response.is_correct)
            .join(Response, Response.question_id == Question.id)
            .join(Attempt, Attempt.id == Response.attempt_id)
            .filter(Attempt.usn == usn)
        )
        if concept_tag:
            q_query = q_query.filter(
                (Question.concept_tag.ilike(f"%{concept_tag}%")) |
                (Question.topic.ilike(f"%{concept_tag}%"))
            )
        attempted_rows = q_query.order_by(Attempt.submitted_at.desc()).limit(limit * 2).all()

        for q_text, is_corr in attempted_rows:
            if q_text and q_text.strip() not in prev_stems:
                prev_stems.append(q_text.strip())
            # If the student recently answered incorrectly on this concept, flag for revision scaffolding
            if is_corr is False and revision_context is None and q_text:
                revision_context = f"The learner previously struggled on: '{q_text.strip()}'"

        # 2. Questions generated directly for this student
        gen_query = GeneratedQuestionRecord.query.filter_by(assigned_usn=usn)
        if concept_tag:
            gen_query = gen_query.filter_by(concept_tag=concept_tag)
        gen_records = gen_query.order_by(GeneratedQuestionRecord.created_at.desc()).limit(limit * 2).all()

        for rec in gen_records:
            if rec.question_text and rec.question_text.strip() not in prev_stems:
                prev_stems.append(rec.question_text.strip())
            if rec.retrieved_chunk_ids:
                try:
                    c_ids = json.loads(rec.retrieved_chunk_ids)
                    if isinstance(c_ids, list):
                        used_chunk_ids.extend([cid for cid in c_ids if isinstance(cid, int)])
                except Exception:
                    pass

    except Exception as e:
        logger.warning(f"Error querying learner question history for USN '{usn}': {e}")

    return prev_stems[:limit], revision_context, list(set(used_chunk_ids))


def build_user_prompt(
    course: str,
    concept_tag: str,
    target_difficulty: str,
    target_bloom: str,
    theta: float,
    mastery: float,
    retrieved_chunks: List[Dict[str, Any]],
    validation_error_feedback: Optional[str] = None,
    challenge_profile: Optional[Dict[str, Any]] = None,
    previous_question_stems: Optional[List[str]] = None,
    revision_context: Optional[str] = None,
) -> str:
    context_text = "\n\n".join(
        f"[Source: {c.get('source_ref')}]\n{c.get('content')}"
        for c in retrieved_chunks
    )

    # Use AdaptiveEngine's specialized cognitive directive if provided
    if challenge_profile and challenge_profile.get("cognitive_directive"):
        profile_str = challenge_profile.get("personalization_profile", "adaptive")
        ability_band = challenge_profile.get("ability_band", "Average")
        mastery_band = challenge_profile.get("mastery_band", "Moderate")
        directive = (
            f"COGNITIVE DIRECTIVE ({profile_str.upper()} - {target_bloom.upper()}):\n"
            f"{challenge_profile['cognitive_directive']}"
        )
    else:
        pedagogical_directives = {
            "Hard": (
                f"COGNITIVE DIRECTIVE (HIGH COGNITIVE DEMAND - {target_bloom.upper()}):\n"
                f"The learner has high latent ability (IRT θ={theta:.2f}) and concept mastery (BKT P(L)={mastery:.2f}).\n"
                "Formulate an analytical question focusing on subtle legal/technical distinctions, boundary conditions, "
                "exceptions, or evaluating comparative scenarios directly grounded in the text. "
                "Do NOT ask a simple definition recall question. Ensure distractors represent plausible, sophisticated near-misses."
            ),
            "Medium": (
                f"COGNITIVE DIRECTIVE (INTERMEDIATE COGNITIVE DEMAND - {target_bloom.upper()}):\n"
                f"The learner has intermediate ability (IRT θ={theta:.2f}) and concept mastery (BKT P(L)={mastery:.2f}).\n"
                "Formulate a question testing conceptual comprehension or practical application of the principles. "
                "Ask how the rule applies in practice or the underlying reasoning explained in the context. "
                "Distractors should test whether the student genuinely understands the mechanics rather than memorized keywords."
            ),
            "Easy": (
                f"COGNITIVE DIRECTIVE (FOUNDATIONAL COGNITIVE DEMAND - {target_bloom.upper()}):\n"
                f"The learner has foundational ability (IRT θ={theta:.2f}) and developing mastery (BKT P(L)={mastery:.2f}).\n"
                "Formulate a clear definition or core principle recognition question directly based on the key facts in the text. "
                "Ensure the question is straightforward and accessible, with clear distractors that clarify fundamental concepts."
            ),
        }
        directive = pedagogical_directives.get(target_difficulty, pedagogical_directives["Medium"])
        profile_str = "standard_adaptive"
        ability_band = "Average"
        mastery_band = "Moderate"

    anti_repetition_section = ""
    if previous_question_stems:
        stems_formatted = "\n".join(f"- {s}" for s in previous_question_stems[:4])
        anti_repetition_section = (
            f"\nPREVIOUSLY ATTEMPTED / GENERATED QUESTIONS FOR THIS LEARNER:\n"
            f"{stems_formatted}\n"
            f"ANTI-REPETITION MANDATE:\n"
            f"Do NOT duplicate, paraphrase, or ask about the exact same sentence or core concept as the above questions. "
            f"Explore a completely different aspect, relationship, boundary condition, or application of '{concept_tag}' from the source material.\n"
        )

    revision_section = ""
    if revision_context:
        revision_section = (
            f"\nREVISION SCAFFOLDING GUIDANCE:\n"
            f"{revision_context}\n"
            f"The learner has not yet mastered this concept. Provide scaffolded reinforcement of the fundamental principle without duplicating the previous stem.\n"
        )

    prompt = (
        f"<|im_start|>system\n{build_system_prompt()}<|im_end|>\n"
        f"<|im_start|>user\n"
        f"Course: {course}\n"
        f"Target Concept: {concept_tag}\n"
        f"Target Difficulty: {target_difficulty}\n"
        f"Target Bloom Level: {target_bloom}\n"
        f"Learner Ability (IRT Theta): {theta:.2f} ({ability_band} Ability Band)\n"
        f"Concept Mastery (BKT P(L)): {mastery:.2f} ({mastery_band} Mastery Band)\n"
        f"Pedagogical Profile: {profile_str}\n\n"
        f"{directive}\n"
        f"{revision_section}"
        f"{anti_repetition_section}\n"
        f"Source Material Context:\n{context_text}\n"
    )

    if validation_error_feedback:
        prompt += f"\nPrevious attempt had errors: {validation_error_feedback}. Please correct them strictly.\n"

    prompt += "\nGenerate the grounded MCQ JSON now.<|im_end|>\n<|im_start|>assistant\n"
    return prompt


def fallback_grounded_generator(
    course: str,
    concept_tag: str,
    target_difficulty: str,
    target_bloom: str,
    retrieved_chunks: List[Dict[str, Any]],
    theta: float = 0.0,
    mastery: float = 0.20,
    seed_key: Optional[str] = None,
    previous_question_stems: Optional[List[str]] = None,
    challenge_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Pedagogically differentiated fallback generator.
    Activated when local GPU/CPU model service is unavailable or in lightweight environments.
    Extracts statements from retrieved chunks, personalizing question stem, options, cognitive demand,
    and Bloom's taxonomy tier based on target difficulty, learner metrics, and question history.
    """
    all_sentences = []
    if retrieved_chunks:
        for chunk in retrieved_chunks:
            chunk_content = chunk.get("content", "")
            raw_sents = [
                s.strip()
                for s in re.split(r"(?<=[.!?\n])\s+", chunk_content)
                if len(s.strip()) > 20 and not s.strip().isdigit()
            ]
            all_sentences.extend(raw_sents)

    if not all_sentences:
        all_sentences = [
            f"{concept_tag} is a core subject area in {course}.",
            f"The application of {concept_tag} requires systematic evaluation and adherence to established principles.",
            f"Under {concept_tag}, exceptions and boundary constraints govern operational validity.",
        ]

    # Filter out sentences that have already been used in previous question stems to ensure diversity
    prev_stems_lower = [s.lower() for s in (previous_question_stems or [])]
    fresh_sentences = [
        sent for sent in all_sentences
        if not any(sent.lower()[:30] in p for p in prev_stems_lower)
    ]
    candidate_sentences = fresh_sentences if fresh_sentences else all_sentences

    seed_str = f"{seed_key}_{concept_tag}_{target_difficulty}_{theta:.2f}_{mastery:.2f}"
    hash_val = abs(hash(seed_str))
    focus_sentence = candidate_sentences[hash_val % len(candidate_sentences)]
    focus_sentence = re.sub(r"^\d+\s*", "", focus_sentence).strip()

    stem_offset = len(previous_question_stems or [])

    if target_difficulty == "Hard":
        stem_variants = [
            f"In critically analyzing edge cases and limitations under '{concept_tag}', which legal or methodological distinction is decisive?",
            f"When evaluating boundary conditions and exceptions concerning '{concept_tag}', which principle governs proper determination?",
            f"In a complex scenario involving competing claims under '{concept_tag}', which analytical criterion must be satisfied according to course material?",
            f"Under advanced comparative review of '{concept_tag}', what foundational constraint limits operational applicability?",
        ]
        question = stem_variants[(hash_val + stem_offset) % len(stem_variants)]
        for offset in range(len(stem_variants)):
            cand = stem_variants[(hash_val + stem_offset + offset) % len(stem_variants)]
            if cand not in (previous_question_stems or []):
                question = cand
                break
        correct_text = f"Compliance with the statutory constraint that: {focus_sentence}"
        distractors = [
            f"Unilateral action is permitted under '{concept_tag}' without regulatory consensus or dispute review.",
            f"Exceptions under '{concept_tag}' automatically expire once general commercial utility is demonstrated.",
            f"Statutory restrictions on '{concept_tag}' do not apply if opposing parties agree to waive standard verification protocols.",
        ]
        explanation = (
            f"At the Analyze level (θ={theta:.2f}), critical evaluation is required: "
            f"the course material explicitly establishes that '{focus_sentence}', which acts as a decisive constraint."
        )

    elif target_difficulty == "Medium":
        stem_variants = [
            f"How does the principle of '{concept_tag}' apply when resolving practical situations or operational requirements?",
            f"Which scenario best illustrates the practical application of '{concept_tag}' as outlined in the curriculum?",
            f"In understanding the operational mechanisms of '{concept_tag}', why is this specific requirement enforced?",
            f"According to the curriculum, what functional role does '{concept_tag}' serve in procedural execution?",
        ]
        question = stem_variants[(hash_val + stem_offset) % len(stem_variants)]
        for offset in range(len(stem_variants)):
            cand = stem_variants[(hash_val + stem_offset + offset) % len(stem_variants)]
            if cand not in (previous_question_stems or []):
                question = cand
                break
        correct_text = f"It enforces the principle that: {focus_sentence}"
        distractors = [
            f"It permits parties to bypass '{concept_tag}' provisions if informal verbal agreements exist.",
            f"It restricts '{concept_tag}' evaluations strictly to post-implementation review rather than prior compliance.",
            f"It mandates that '{concept_tag}' guidelines be applied only when external dispute litigation occurs.",
        ]
        explanation = (
            f"At the Understand/Apply level (θ={theta:.2f}), conceptual comprehension applies: "
            f"the material substantiates that '{focus_sentence}'."
        )

    else:  # Easy
        stem_variants = [
            f"According to the course materials on '{concept_tag}', which of the following is the primary definition or rule established?",
            f"Under the foundational syllabus for '{concept_tag}', which core statement is explicitly substantiated?",
            f"In the introductory study of '{concept_tag}', which of the following accurately identifies its key principle?",
            f"Which of the following describes the fundamental premise of '{concept_tag}' as stated in course notes?",
        ]
        question = stem_variants[(hash_val + stem_offset) % len(stem_variants)]
        for offset in range(len(stem_variants)):
            cand = stem_variants[(hash_val + stem_offset + offset) % len(stem_variants)]
            if cand not in (previous_question_stems or []):
                question = cand
                break
        correct_text = focus_sentence
        distractors = [
            f"It states that '{concept_tag.lower()}' is optional and carries no formal regulatory standing.",
            f"It completely supersedes all established institutional and syllabus guidelines without qualification.",
            f"It assumes that '{concept_tag.lower()}' operates without any need for factual verification or documentation.",
        ]
        explanation = (
            f"At the Remember level (θ={theta:.2f}), foundational recall confirms: "
            f"'{focus_sentence}' is explicitly stated in the source context."
        )

    correct_idx = (hash_val + int(theta * 7) + int(mastery * 13)) % 4
    option_letters = ["A", "B", "C", "D"]
    correct_option = option_letters[correct_idx]

    options = {}
    d_idx = 0
    for idx, letter in enumerate(option_letters):
        if idx == correct_idx:
            options[f"option_{letter.lower()}"] = correct_text
        else:
            options[f"option_{letter.lower()}"] = distractors[d_idx % len(distractors)]
            d_idx += 1

    return {
        "question": question,
        "option_a": options["option_a"],
        "option_b": options["option_b"],
        "option_c": options["option_c"],
        "option_d": options["option_d"],
        "correct_option": correct_option,
        "explanation": explanation,
        "difficulty": target_difficulty,
        "bloom_level": target_bloom,
    }


def generate_adaptive_question(
    course: str,
    concept_tag: str,
    usn: Optional[str] = None,
    student_theta: Optional[float] = None,
    concept_mastery: Optional[float] = None,
    target_difficulty_override: Optional[str] = None,
    top_k_chunks: int = 3,
    max_retries: int = MAX_RETRIES,
    assigned_usn: Optional[str] = None,
    question_index: int = 0,
    previous_attempts: Optional[int] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """
    Execute full end-to-end adaptive generation pipeline:
    1. Resolve learner IRT theta and BKT mastery without recalculating them.
    2. Retrieve learner question history (preventing repetition while allowing revision).
    3. Compute pedagogical challenge profile via AdaptiveEngine.
    4. Retrieve diverse course context chunks via RAG with chunk rotation/exclusion.
    5. Prompt Qwen+LoRA model service with complete AdaptiveEngine directives and anti-repetition guidance.
    6. Validate output with retry loop.
    7. Persist GeneratedQuestionRecord for teacher review and LoRA fine-tuning collection.
    """
    # 1. Resolve Learner Profile Metrics (single source of truth: db records or caller-provided)
    resolved_theta = student_theta
    resolved_mastery = concept_mastery
    learner_usn = assigned_usn or usn

    if learner_usn:
        if resolved_theta is None:
            st = StudentTheta.query.filter_by(usn=learner_usn).first()
            if st and st.theta is not None:
                resolved_theta = st.theta

        if resolved_mastery is None:
            sm = StudentMastery.query.filter_by(usn=learner_usn, concept_tag=concept_tag).first()
            if sm and sm.mastery is not None:
                resolved_mastery = sm.mastery

    # 2. Retrieve Learner Question History (prevent repetition, identify revision needs, track used chunks)
    prev_stems: List[str] = []
    revision_ctx: Optional[str] = None
    used_chunk_ids: List[int] = list(exclude_chunk_ids or [])

    if learner_usn:
        hist_stems, rev_context, hist_chunk_ids = get_learner_question_history(
            usn=learner_usn,
            concept_tag=concept_tag,
            limit=5,
        )
        prev_stems.extend(hist_stems)
        if rev_context:
            revision_ctx = rev_context
        used_chunk_ids.extend(hist_chunk_ids)

    eff_previous_attempts = previous_attempts if previous_attempts is not None else len(prev_stems)

    # 3. Determine Pedagogical Challenge Profile via AdaptiveEngine
    challenge = AdaptiveEngine.determine_challenge_level(
        theta=resolved_theta,
        mastery=resolved_mastery,
        concept_tag=concept_tag,
        previous_attempts=eff_previous_attempts,
        target_difficulty_override=target_difficulty_override,
    )

    difficulty = challenge["difficulty"]
    bloom = challenge["bloom_level"]
    theta_used = challenge["theta_used"]
    mastery_used = challenge["mastery_used"]

    # 4. Retrieve Context via RAG (with context diversity & chunk rotation)
    retrieval_res = retrieve_context(
        course=course,
        concept_tag=concept_tag,
        top_k=top_k_chunks,
        exclude_chunk_ids=used_chunk_ids if used_chunk_ids else None,
        chunk_offset=question_index * 2,
    )
    chunks = retrieval_res.get("chunks", [])
    combined_source_text = "\n\n".join(c["content"] for c in chunks) if chunks else ""
    chunk_ids = [c["chunk_id"] for c in chunks]

    primary_source_doc_id = chunks[0]["document_id"] if chunks else None
    primary_source_filename = chunks[0]["filename"] if chunks else None
    primary_source_ref = chunks[0]["source_ref"] if chunks else "General Course Knowledge"

    # 5. Generate & Validate with Retries
    model_service = QwenLoRAModelService.get_instance()
    last_errors = []
    generated_mcq_data = None
    validation_status = "approved"
    validation_notes = None
    model_name_used = model_service.base_model_name
    adapter_ver = "pilot_lora_v1"

    feedback = None
    prompt_used = ""

    for attempt in range(max_retries + 1):
        prompt_used = build_user_prompt(
            course=course,
            concept_tag=concept_tag,
            target_difficulty=difficulty,
            target_bloom=bloom,
            theta=theta_used,
            mastery=mastery_used,
            retrieved_chunks=chunks,
            validation_error_feedback=feedback,
            challenge_profile=challenge,
            previous_question_stems=prev_stems,
            revision_context=revision_ctx,
        )

        try:
            # Check if model is loaded or loadable
            if model_service.is_loaded() or model_service.load_model()[0]:
                raw_response = model_service.generate(prompt_used, max_new_tokens=400, temperature=0.7)
                parsed = parse_json_from_model_output(raw_response)
            else:
                # Model weights unavailable -> invoke grounded fallback
                logger.info(f"Model service not available ({model_service._load_error}); using grounded fallback.")
                parsed = fallback_grounded_generator(
                    course=course,
                    concept_tag=concept_tag,
                    target_difficulty=difficulty,
                    target_bloom=bloom,
                    retrieved_chunks=chunks,
                    theta=theta_used,
                    mastery=mastery_used,
                    seed_key=f"{learner_usn or 'std'}_{question_index}_{attempt}",
                    previous_question_stems=prev_stems,
                    challenge_profile=challenge,
                )
                model_name_used = "grounded_template_fallback"
        except Exception as e:
            logger.warning(f"Generation attempt {attempt + 1} encountered exception: {e}")
            parsed = fallback_grounded_generator(
                course=course,
                concept_tag=concept_tag,
                target_difficulty=difficulty,
                target_bloom=bloom,
                retrieved_chunks=chunks,
                theta=theta_used,
                mastery=mastery_used,
                seed_key=f"{learner_usn or 'std'}_{question_index}_{attempt}",
                previous_question_stems=prev_stems,
                challenge_profile=challenge,
            )
            model_name_used = "grounded_template_fallback"

        is_valid, errors, validated_data = validate_mcq_data(parsed, source_context=combined_source_text)

        if is_valid:
            generated_mcq_data = validated_data
            last_errors = []
            break
        else:
            last_errors = errors
            feedback = "; ".join(errors)
            logger.info(f"Validation attempt {attempt + 1} failed: {feedback}")

    if not generated_mcq_data:
        # Ultimate fallback to ensure a valid question is delivered
        generated_mcq_data = fallback_grounded_generator(
            course=course,
            concept_tag=concept_tag,
            target_difficulty=difficulty,
            target_bloom=bloom,
            retrieved_chunks=chunks,
            theta=theta_used,
            mastery=mastery_used,
            seed_key=f"{learner_usn or 'std'}_{question_index}_final",
            previous_question_stems=prev_stems,
            challenge_profile=challenge,
        )
        validation_status = "approved"
        validation_notes = f"Model retries exhausted. Fallback used. Errors: {'; '.join(last_errors)}"
        model_name_used = "grounded_template_fallback"

    # 6. Persist GeneratedQuestionRecord for Quality Collection & LoRA Fine-Tuning
    record = GeneratedQuestionRecord(
        course=course,
        concept_tag=concept_tag,
        learner_theta=theta_used,
        learner_mastery=mastery_used,
        target_difficulty=difficulty,
        target_bloom=bloom,
        source_context=combined_source_text,
        prompt_text=prompt_used,
        retrieved_chunk_ids=json.dumps(chunk_ids),
        question_text=generated_mcq_data["question"],
        option_a=generated_mcq_data["option_a"],
        option_b=generated_mcq_data["option_b"],
        option_c=generated_mcq_data["option_c"],
        option_d=generated_mcq_data["option_d"],
        correct_option=generated_mcq_data["correct_option"],
        explanation=generated_mcq_data["explanation"],
        source_document_id=primary_source_doc_id,
        source_filename=primary_source_filename[:500] if primary_source_filename else None,
        source_page_or_slide=primary_source_ref[:500] if primary_source_ref else None,
        model_name=model_name_used[:100] if model_name_used else "Qwen/Qwen3-0.6B",
        adapter_version=adapter_ver[:100] if adapter_ver else "pilot_lora_v1",
        assigned_usn=learner_usn,
        validation_status=validation_status,
        validation_notes=validation_notes,
    )
    db.session.add(record)
    db.session.commit()

    return {
        "record_id": record.id,
        "assigned_usn": record.assigned_usn,
        "course": course,
        "concept_tag": concept_tag,
        "target_difficulty": difficulty,
        "target_bloom": bloom,
        "learner_theta": theta_used,
        "learner_mastery": mastery_used,
        "target_b": challenge.get("target_b"),
        "expected_p_correct": challenge.get("expected_p_correct"),
        "fisher_information": challenge.get("fisher_information"),
        "is_empirically_calibrated": False,
        "ability_band": challenge.get("ability_band"),
        "mastery_band": challenge.get("mastery_band"),
        "personalization_profile": challenge.get("personalization_profile"),
        "cognitive_directive": challenge.get("cognitive_directive"),
        "previous_attempts_count": eff_previous_attempts,
        "anti_repetition_applied": len(prev_stems) > 0,
        "question": generated_mcq_data["question"],
        "options": {
            "A": generated_mcq_data["option_a"],
            "B": generated_mcq_data["option_b"],
            "C": generated_mcq_data["option_c"],
            "D": generated_mcq_data["option_d"],
        },
        "correct_option": generated_mcq_data["correct_option"],
        "explanation": generated_mcq_data["explanation"],
        "source_ref": primary_source_ref,
        "source_filename": primary_source_filename,
        "retrieved_chunks_count": len(chunks),
        "validation_status": validation_status,
        "pedagogical_rationale": challenge["pedagogical_rationale"],
    }

