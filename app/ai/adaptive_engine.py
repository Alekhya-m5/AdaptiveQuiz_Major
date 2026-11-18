"""
Adaptive Difficulty Engine.
Maps learner IRT theta (overall ability) and BKT mastery P(L) (concept-specific knowledge)
to an optimal pedagogical challenge level (Difficulty and Bloom's Taxonomy level).
"""

from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

# Default priors when learner data is not yet established
DEFAULT_PRIOR_THETA = 0.0
DEFAULT_PRIOR_MASTERY = 0.20


class AdaptiveEngine:
    """
    Pedagogical decision engine combining Rasch IRT ability and Corbett-Anderson BKT.
    Separated from the LLM prompt so it is deterministic, transparent, and independently unit-testable.
    """

    @classmethod
    def determine_challenge_level(
        cls,
        theta: Optional[float] = None,
        mastery: Optional[float] = None,
        concept_tag: Optional[str] = None,
        previous_attempts: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Determine target question difficulty and Bloom's taxonomy tier.

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
                "pedagogical_rationale": str
            }
        """
        effective_theta = float(theta) if (theta is not None) else DEFAULT_PRIOR_THETA
        effective_mastery = float(mastery) if (mastery is not None) else DEFAULT_PRIOR_MASTERY

        # Clamp values to valid mathematical ranges
        effective_theta = max(-4.0, min(4.0, effective_theta))
        effective_mastery = max(0.0, min(1.0, effective_mastery))

        # Pedagogical mapping rules
        if effective_theta >= 1.0:  # High general ability
            if effective_mastery >= 0.70:
                difficulty = "Hard"
                bloom = "Analyze"
                rationale = (
                    f"High ability (θ={effective_theta:.2f}) and mastered concept (P(L)={effective_mastery:.2f}). "
                    "Targeting higher-order analysis and complex problem scenarios."
                )
            elif effective_mastery >= 0.40:
                difficulty = "Medium"
                bloom = "Apply"
                rationale = (
                    f"High ability (θ={effective_theta:.2f}) with developing mastery (P(L)={effective_mastery:.2f}). "
                    "Targeting practical application to reinforce concept mechanics."
                )
            else:
                difficulty = "Medium"
                bloom = "Understand"
                rationale = (
                    f"High ability (θ={effective_theta:.2f}) but low concept mastery (P(L)={effective_mastery:.2f}). "
                    "Focusing on conceptual understanding to resolve specific foundational gaps."
                )

        elif effective_theta >= 0.0:  # Average / Developing ability
            if effective_mastery >= 0.70:
                difficulty = "Medium"
                bloom = "Apply"
                rationale = (
                    f"Proficient mastery (P(L)={effective_mastery:.2f}) with average ability (θ={effective_theta:.2f}). "
                    "Targeting application-level challenge."
                )
            elif effective_mastery >= 0.40:
                difficulty = "Medium"
                bloom = "Understand"
                rationale = (
                    f"Developing ability (θ={effective_theta:.2f}) and developing mastery (P(L)={effective_mastery:.2f}). "
                    "Targeting balanced conceptual comprehension."
                )
            else:
                difficulty = "Easy"
                bloom = "Remember"
                rationale = (
                    f"Average ability (θ={effective_theta:.2f}) but low concept mastery (P(L)={effective_mastery:.2f}). "
                    "Targeting foundational recall and core definitions."
                )

        else:  # Low ability / At risk (θ < 0.0)
            if effective_mastery >= 0.70:
                difficulty = "Medium"
                bloom = "Understand"
                rationale = (
                    f"Demonstrated concept mastery (P(L)={effective_mastery:.2f}) despite lower overall ability (θ={effective_theta:.2f}). "
                    "Reinforcing conceptual comprehension at medium difficulty."
                )
            else:
                difficulty = "Easy"
                bloom = "Remember"
                rationale = (
                    f"Lower ability (θ={effective_theta:.2f}) and unmastered concept (P(L)={effective_mastery:.2f}). "
                    "Scaffolding with foundational definition and recall questions."
                )

        difficulty_numeric_map = {"Easy": -1.0, "Medium": 0.0, "Hard": 1.0}

        return {
            "difficulty": difficulty,
            "difficulty_numeric": difficulty_numeric_map.get(difficulty, 0.0),
            "bloom_level": bloom,
            "theta_used": round(effective_theta, 4),
            "mastery_used": round(effective_mastery, 4),
            "pedagogical_rationale": rationale,
        }

