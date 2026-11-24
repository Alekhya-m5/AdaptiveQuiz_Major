"""
Adaptive Difficulty Engine.
Maps learner IRT theta (overall ability) and BKT mastery P(L) (concept-specific knowledge)
to an optimal pedagogical challenge level (Difficulty, Bloom's Taxonomy level, and Cognitive Directive).
"""

from typing import Dict, Any, Optional
import math
import logging

logger = logging.getLogger(__name__)

# Default priors when learner data is not yet established
DEFAULT_PRIOR_THETA = 0.0
DEFAULT_PRIOR_MASTERY = 0.20

# Ability thresholds (IRT theta scale)
THETA_HIGH_THRESHOLD = 1.0
THETA_LOW_THRESHOLD = 0.0

# Mastery thresholds (BKT probability scale [0.0, 1.0])
MASTERY_HIGH_THRESHOLD = 0.70
MASTERY_MODERATE_THRESHOLD = 0.40


class AdaptiveEngine:
    """
    Pedagogical decision engine combining Rasch IRT ability and Corbett-Anderson BKT.
    Separated from the LLM prompt so it is deterministic, transparent, and independently unit-testable.
    """

    @classmethod
    def calculate_target_irt_difficulty(
        cls,
        theta: float,
        mastery: float,
        target_difficulty: str,
    ) -> Dict[str, Any]:
        """
        Compute continuous IRT target difficulty and expected Fisher Information.
        Distinguishes continuous target difficulty from categorical discrete labels.
        Under the Rasch 1PL model:
          P(correct | theta, b) = 1 / (1 + exp(-(theta - b)))
          I(theta, b) = P * (1 - P)  [Maximized at b = theta, where I = 0.25]
        """
        # When mastery is high (>= 0.70), nudge target b above theta to challenge
        # When mastery is low (<= 0.30), nudge target b below theta to scaffold
        # Otherwise, center around theta for optimal Fisher Information
        if mastery >= MASTERY_HIGH_THRESHOLD:
            b_offset = 0.35
        elif mastery <= 0.30:
            b_offset = -0.35
        else:
            b_offset = 0.0

        # Account for explicit categorical target tier if provided
        if target_difficulty == "Hard" and theta < 0.5:
            b_target = max(theta + 0.4, 0.75)
        elif target_difficulty == "Easy" and theta > -0.5:
            b_target = min(theta - 0.4, -0.75)
        else:
            b_target = theta + b_offset

        # Clamp to realistic psychometric range [-3.5, 3.5]
        b_target = max(-3.5, min(3.5, b_target))

        diff_z = theta - b_target
        diff_clipped = max(-35.0, min(35.0, diff_z))
        p_correct = 1.0 / (1.0 + math.exp(-diff_clipped))
        fisher_info = p_correct * (1.0 - p_correct)

        return {
            "target_b": round(b_target, 4),
            "expected_p_correct": round(p_correct, 4),
            "fisher_information": round(fisher_info, 4),
            "is_empirically_calibrated": False,  # Newly generated items are uncalibrated target priors
        }

    @classmethod
    def select_best_calibrated_item(
        cls,
        theta: float,
        calibrated_items: list,
        exclude_item_ids: Optional[list] = None,
        min_responses: int = 5,
    ) -> Optional[Dict[str, Any]]:
        """
        Given a list of calibrated items (each having 'difficulty_b', 'question_id', 'total_responses'),
        select the item that maximizes Fisher Information I(theta, b) at the learner's theta,
        excluding items the learner has already attempted.
        """
        exclude_set = set(exclude_item_ids or [])
        valid_items = [
            it for it in calibrated_items
            if it.get("question_id") not in exclude_set and it.get("total_responses", 0) >= min_responses
        ]
        if not valid_items:
            return None

        best_item = None
        best_info = -1.0

        for item in valid_items:
            b = float(item.get("difficulty_b", 0.0))
            diff_z = max(-35.0, min(35.0, theta - b))
            p = 1.0 / (1.0 + math.exp(-diff_z))
            info = p * (1.0 - p)
            if info > best_info:
                best_info = info
                best_item = {**item, "fisher_info_at_theta": round(info, 4)}

        return best_item

    @classmethod
    def determine_challenge_level(
        cls,
        theta: Optional[float] = None,
        mastery: Optional[float] = None,
        concept_tag: Optional[str] = None,
        previous_attempts: Optional[int] = None,
        target_difficulty_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Determine target question difficulty, Bloom's taxonomy tier, and cognitive directive.

        Parameters
        ----------
        theta : float, optional
            Student's overall latent ability estimate from StudentTheta.
        mastery : float, optional
            Student's current concept mastery probability from StudentMastery.
        concept_tag : str, optional
            Name of the concept being targeted.
        previous_attempts : int, optional
            Number of previous attempts on this concept.

        Returns
        -------
        Dict[str, Any]
            {
                "difficulty": "Easy" | "Medium" | "Hard",
                "difficulty_numeric": float (-1.0, 0.0, 1.0),
                "bloom_level": "Remember" | "Understand" | "Apply" | "Analyze",
                "theta_used": float,
                "mastery_used": float,
                "pedagogical_rationale": str,
                "ability_band": "Low" | "Average" | "High",
                "mastery_band": "Low" | "Moderate" | "High",
                "personalization_profile": str,
                "cognitive_directive": str,
                "theta_source": "measured" | "prior",
                "mastery_source": "measured" | "prior",
            }
        """
        # 1. Parse and sanitize IRT theta
        theta_source = "measured"
        try:
            if theta is None or (isinstance(theta, float) and math.isnan(theta)):
                effective_theta = DEFAULT_PRIOR_THETA
                theta_source = "prior"
            else:
                effective_theta = float(theta)
        except (ValueError, TypeError):
            effective_theta = DEFAULT_PRIOR_THETA
            theta_source = "prior"

        # 2. Parse and sanitize BKT mastery
        mastery_source = "measured"
        try:
            if mastery is None or (isinstance(mastery, float) and math.isnan(mastery)):
                effective_mastery = DEFAULT_PRIOR_MASTERY
                mastery_source = "prior"
            else:
                effective_mastery = float(mastery)
        except (ValueError, TypeError):
            effective_mastery = DEFAULT_PRIOR_MASTERY
            mastery_source = "prior"

        # 3. Clamp values to valid psychometric ranges
        effective_theta = max(-4.0, min(4.0, effective_theta))
        effective_mastery = max(0.0, min(1.0, effective_mastery))

        # 4. Classify bands
        if effective_theta >= THETA_HIGH_THRESHOLD:
            ability_band = "High"
        elif effective_theta >= THETA_LOW_THRESHOLD:
            ability_band = "Average"
        else:
            ability_band = "Low"

        if effective_mastery >= MASTERY_HIGH_THRESHOLD:
            mastery_band = "High"
        elif effective_mastery >= MASTERY_MODERATE_THRESHOLD:
            mastery_band = "Moderate"
        else:
            mastery_band = "Low"

        concept_label = f"'{concept_tag}'" if concept_tag else "the target concept"

        # 5. Determine Pedagogical Profile, Difficulty, Bloom Level & Directives
        # Profile 1: High Ability + High Mastery
        if ability_band == "High" and mastery_band == "High":
            difficulty = "Hard"
            bloom = "Analyze"
            personalization_profile = "advanced_challenge"
            cognitive_directive = (
                f"High-order analytical challenge: learner demonstrates both strong latent ability (θ={effective_theta:.2f}) "
                f"and mastered concept knowledge (P(L)={effective_mastery:.2f}). Formulate a rigorous problem requiring "
                f"critical analysis of edge cases, boundary conditions, or multi-step trade-offs in {concept_label}."
            )
            rationale = (
                f"High ability (θ={effective_theta:.2f}) and mastered concept (P(L)={effective_mastery:.2f}). "
                "Targeting higher-order analysis and complex problem scenarios."
            )

        # Profile 2: High Ability + Low Mastery (or Unassessed)
        elif ability_band == "High" and mastery_band == "Low":
            difficulty = "Medium"
            bloom = "Understand"
            if mastery_source == "prior":
                personalization_profile = "high_ability_untested_topic"
                cognitive_directive = (
                    f"Rapid conceptual bridging (untested topic): learner has high general ability (θ={effective_theta:.2f}), "
                    f"but {concept_label} has no prior assessment data (using baseline P(L)={effective_mastery:.2f}). "
                    "Formulate a clear conceptual understanding question to establish the learner's domain baseline."
                )
                rationale = (
                    f"High ability (θ={effective_theta:.2f}) with unassessed concept (prior P(L)={effective_mastery:.2f}). "
                    "Establishing domain baseline via conceptual comprehension."
                )
            else:
                personalization_profile = "accelerated_acquisition"
                cognitive_directive = (
                    f"Accelerated concept acquisition: learner has high analytical capacity (θ={effective_theta:.2f}) "
                    f"but low assessed mastery (P(L)={effective_mastery:.2f}) in {concept_label}. "
                    "Focus on conceptual comprehension and core mechanics to rapidly resolve foundational gaps."
                )
                rationale = (
                    f"High ability (θ={effective_theta:.2f}) but low concept mastery (P(L)={effective_mastery:.2f}). "
                    "Focusing on conceptual understanding to resolve specific foundational gaps."
                )

        # High Ability + Moderate Mastery
        elif ability_band == "High" and mastery_band == "Moderate":
            difficulty = "Medium"
            bloom = "Apply"
            personalization_profile = "advanced_application"
            cognitive_directive = (
                f"Practical application challenge: high-ability learner (θ={effective_theta:.2f}) with developing mastery "
                f"(P(L)={effective_mastery:.2f}). Formulate a scenario testing direct application of {concept_label} principles."
            )
            rationale = (
                f"High ability (θ={effective_theta:.2f}) with developing mastery (P(L)={effective_mastery:.2f}). "
                "Targeting practical application to reinforce concept mechanics."
            )

        # Profile 3: Low Ability + High Mastery
        elif ability_band == "Low" and mastery_band == "High":
            difficulty = "Medium"
            bloom = "Understand"
            personalization_profile = "anchored_reinforcement"
            cognitive_directive = (
                f"Anchored reinforcement: learner exhibits lower overall test ability (θ={effective_theta:.2f}) "
                f"but has achieved high mastery (P(L)={effective_mastery:.2f}) in {concept_label}. "
                "Reinforce conceptual comprehension with accessible language; avoid convoluted syntactical trickery."
            )
            rationale = (
                f"Demonstrated concept mastery (P(L)={effective_mastery:.2f}) despite lower overall ability (θ={effective_theta:.2f}). "
                "Reinforcing conceptual comprehension at medium difficulty."
            )

        # Profile 4: Low Ability + Low Mastery
        elif ability_band == "Low" and mastery_band == "Low":
            difficulty = "Easy"
            bloom = "Remember"
            personalization_profile = "foundational_scaffolding"
            cognitive_directive = (
                f"Foundational scaffolding: learner has low overall ability (θ={effective_theta:.2f}) and low concept mastery "
                f"(P(L)={effective_mastery:.2f}). Target direct definition recall and basic recognition of key terms in {concept_label}."
            )
            rationale = (
                f"Lower ability (θ={effective_theta:.2f}) and unmastered concept (P(L)={effective_mastery:.2f}). "
                "Scaffolding with foundational definition and recall questions."
            )

        # Low Ability + Moderate Mastery
        elif ability_band == "Low" and mastery_band == "Moderate":
            difficulty = "Easy"
            bloom = "Remember"
            personalization_profile = "guided_reinforcement"
            cognitive_directive = (
                f"Guided reinforcement: lower ability learner (θ={effective_theta:.2f}) with moderate concept familiarity "
                f"(P(L)={effective_mastery:.2f}). Reinforce foundational definitions before escalating cognitive complexity."
            )
            rationale = (
                f"Lower ability (θ={effective_theta:.2f}) and developing mastery (P(L)={effective_mastery:.2f}). "
                "Scaffolding with foundational recall to consolidate knowledge."
            )

        # Profile 5: Average Ability + Average (Moderate) Mastery
        elif ability_band == "Average" and mastery_band == "Moderate":
            difficulty = "Medium"
            bloom = "Understand"
            personalization_profile = "balanced_development"
            cognitive_directive = (
                f"Balanced development: average ability (θ={effective_theta:.2f}) and developing mastery (P(L)={effective_mastery:.2f}). "
                f"Target balanced conceptual comprehension and functional understanding of {concept_label}."
            )
            rationale = (
                f"Developing ability (θ={effective_theta:.2f}) and developing mastery (P(L)={effective_mastery:.2f}). "
                "Targeting balanced conceptual comprehension."
            )

        # Average Ability + High Mastery
        elif ability_band == "Average" and mastery_band == "High":
            difficulty = "Medium"
            bloom = "Apply"
            personalization_profile = "proficient_application"
            cognitive_directive = (
                f"Proficient application: learner has average ability (θ={effective_theta:.2f}) and high mastery "
                f"(P(L)={effective_mastery:.2f}). Formulate a scenario testing application of {concept_label} in context."
            )
            rationale = (
                f"Proficient mastery (P(L)={effective_mastery:.2f}) with average ability (θ={effective_theta:.2f}). "
                "Targeting application-level challenge."
            )

        # Average Ability + Low Mastery (or Unassessed)
        else:  # ability_band == "Average" and mastery_band == "Low"
            difficulty = "Easy"
            bloom = "Remember"
            if mastery_source == "prior":
                personalization_profile = "untested_prior_baseline"
                cognitive_directive = (
                    f"Baseline assessment (untested topic): average ability learner (θ={effective_theta:.2f}) encountering "
                    f"{concept_label} without prior assessment data (P(L)={effective_mastery:.2f}). "
                    "Start with foundational definition recall to establish mastery."
                )
                rationale = (
                    f"Average ability (θ={effective_theta:.2f}) but unassessed concept (prior P(L)={effective_mastery:.2f}). "
                    "Targeting foundational recall and core definitions."
                )
            else:
                personalization_profile = "foundational_recall"
                cognitive_directive = (
                    f"Foundational recall: learner has average ability (θ={effective_theta:.2f}) but low assessed mastery "
                    f"(P(L)={effective_mastery:.2f}). Focus on basic definitions and core facts in {concept_label}."
                )
                rationale = (
                    f"Average ability (θ={effective_theta:.2f}) but low concept mastery (P(L)={effective_mastery:.2f}). "
                    "Targeting foundational recall and core definitions."
                )

        # Handle explicit target difficulty override if provided
        if target_difficulty_override in ("Easy", "Medium", "Hard"):
            difficulty = target_difficulty_override
            if target_difficulty_override == "Easy":
                bloom = "Remember"
            elif target_difficulty_override == "Medium":
                bloom = "Understand" if effective_mastery < 0.6 else "Apply"
            elif target_difficulty_override == "Hard":
                bloom = "Analyze"
            cognitive_directive = f"{cognitive_directive} (Target tier manually set to {target_difficulty_override})."

        # Incorporate previous attempts into directive if learner is practicing repeatedly
        if previous_attempts and previous_attempts > 0:
            cognitive_directive = (
                f"{cognitive_directive} Note: learner has attempted {previous_attempts} prior question(s) "
                f"on {concept_label}. Ensure this question targets a distinct aspect or novel application scenario."
            )

        # Compute continuous target IRT parameters and Fisher information
        irt_metrics = cls.calculate_target_irt_difficulty(
            theta=effective_theta,
            mastery=effective_mastery,
            target_difficulty=difficulty,
        )

        difficulty_numeric_map = {"Easy": -1.0, "Medium": 0.0, "Hard": 1.0}

        return {
            "difficulty": difficulty,
            "difficulty_numeric": difficulty_numeric_map.get(difficulty, 0.0),
            "bloom_level": bloom,
            "theta_used": round(effective_theta, 4),
            "mastery_used": round(effective_mastery, 4),
            "target_b": irt_metrics["target_b"],
            "expected_p_correct": irt_metrics["expected_p_correct"],
            "fisher_information": irt_metrics["fisher_information"],
            "is_empirically_calibrated": False,
            "pedagogical_rationale": rationale,
            "ability_band": ability_band,
            "mastery_band": mastery_band,
            "personalization_profile": personalization_profile,
            "cognitive_directive": cognitive_directive,
            "theta_source": theta_source,
            "mastery_source": mastery_source,
            "previous_attempts": previous_attempts or 0,
        }

