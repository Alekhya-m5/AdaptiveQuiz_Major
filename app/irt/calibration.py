"""
Rasch / 1PL Item Difficulty Calibration and Learner Ability (Theta) Estimation.

Implements defensible psychometric batch estimation:
- Item Difficulty calibration via PROX (Normal Approximation Algorithm) with scale anchoring (mean b = 0).
- Learner Ability estimation via Newton-Raphson Maximum Likelihood Estimation (MLE).
- Explicit handling of extreme scores (all correct/incorrect), insufficient data cold start,
  and scale identifiability.
"""

import math
from typing import Dict, List, Any
from app.irt.probability import rasch_probability, rasch_information


def calibrate_items(
    responses: List[Dict[str, Any]],
    min_responses: int = 5,
    method: str = "PROX",
) -> Dict[str, Dict[str, Any]]:
    """
    Calibrate item difficulty parameters b_i from population response data.

    Parameters
    ----------
    responses : List[Dict[str, Any]]
        List of response records with keys:
        'question_id', 'is_correct', 'is_unanswered', 'quiz', 'concept_tag', 'bloom_level'.
    min_responses : int, default 5
        Minimum responses required to calibrate an item.
    method : str, default 'PROX'
        Calibration algorithm.

    Returns
    -------
    Dict[str, Dict[str, Any]]
        Mapping of question_id -> calibration summary dict.
    """
    # Group responses by item
    items: Dict[str, Dict[str, Any]] = {}
    for r in responses:
        qid = r["question_id"]
        if qid not in items:
            items[qid] = {
                "question_id": qid,
                "quiz": r.get("quiz"),
                "concept_tag": r.get("concept_tag"),
                "bloom_level": r.get("bloom_level"),
                "total": 0,
                "correct": 0,
                "unanswered": 0,
            }
        items[qid]["total"] += 1
        if r.get("is_correct"):
            items[qid]["correct"] += 1
        if r.get("is_unanswered"):
            items[qid]["unanswered"] += 1

    calibrations: Dict[str, Dict[str, Any]] = {}
    valid_logits = []

    # Step 1: Compute initial logits and identify edge cases
    for qid, item in items.items():
        total = item["total"]
        correct = item["correct"]
        unanswered = item["unanswered"]

        p_val = correct / total if total > 0 else 0.0

        if total < min_responses:
            calibrations[qid] = {
                **item,
                "proportion_correct": round(p_val, 4),
                "difficulty_b": 0.0,
                "std_error": None,
                "calibration_status": "INSUFFICIENT_DATA",
                "calibration_method": method,
                "notes": f"Total responses ({total}) below threshold ({min_responses}).",
            }
            continue

        # All correct: extreme ceiling
        if correct == total:
            adj_correct = total - 0.5
            raw_logit = math.log((total - adj_correct) / adj_correct)
            se = math.sqrt(total / (adj_correct * 0.5))
            status = "ALL_CORRECT"
        # All incorrect: extreme floor
        elif correct == 0:
            adj_correct = 0.5
            raw_logit = math.log((total - adj_correct) / adj_correct)
            se = math.sqrt(total / (0.5 * (total - 0.5)))
            status = "ALL_INCORRECT"
        else:
            raw_logit = math.log((total - correct) / correct)
            se = math.sqrt(total / (correct * (total - correct)))
            status = "CALIBRATED"

        valid_logits.append(raw_logit)
        calibrations[qid] = {
            **item,
            "proportion_correct": round(p_val, 4),
            "raw_logit": raw_logit,
            "std_error": round(se, 4),
            "calibration_status": status,
            "calibration_method": method,
        }

    # Step 2: Model Identifiability & Scale Anchoring
    # In the Rasch model, ability and difficulty share an interval scale with an arbitrary origin.
    # We resolve location indeterminacy by anchoring the mean item difficulty to 0.0 logits (sum(b_i) = 0).
    mean_logit = sum(valid_logits) / len(valid_logits) if valid_logits else 0.0

    for qid, res in calibrations.items():
        if res.get("raw_logit") is not None:
            # Centered difficulty b_i
            res["difficulty_b"] = round(res["raw_logit"] - mean_logit, 4)
            del res["raw_logit"]
        else:
            res["difficulty_b"] = 0.0

    return calibrations


def estimate_theta_mle(
    responses: List[Dict[str, Any]],
    item_difficulties: Dict[str, float],
    min_items: int = 1,
    max_iter: int = 50,
    tol: float = 1e-4,
) -> Dict[str, Any]:
    """
    Estimate learner ability (theta) using Newton-Raphson Maximum Likelihood Estimation (MLE).

    Parameters
    ----------
    responses : List[Dict[str, Any]]
        List of response records for a single learner with keys: 'question_id', 'is_correct'.
    item_difficulties : Dict[str, float]
        Mapping of question_id -> calibrated difficulty b.
    min_items : int, default 1
        Minimum answered items required.
    max_iter : int, default 50
        Maximum Newton-Raphson iterations.
    tol : float, default 1e-4
        Convergence tolerance on step size |Delta theta|.

    Returns
    -------
    Dict[str, Any]
        {
            "theta": float,
            "std_error": float or None,
            "status": str ("ESTIMATED", "ALL_CORRECT", "ALL_INCORRECT", "INSUFFICIENT_DATA"),
            "responses_count": int,
            "correct_count": int
        }
    """
    valid_items = []
    for r in responses:
        qid = r["question_id"]
        b = item_difficulties.get(qid, 0.0)
        u = 1 if r.get("is_correct") else 0
        valid_items.append((b, u))

    n_items = len(valid_items)
    if n_items < min_items:
        return {
            "theta": 0.0,
            "std_error": None,
            "status": "INSUFFICIENT_DATA",
            "responses_count": n_items,
            "correct_count": 0,
        }

    correct_count = sum(u for _, u in valid_items)

    # Extreme score handling
    # In pure MLE, perfect or zero scores yield +/- infinity.
    # Standard psychometric practice applies a score offset (e.g. 0.3 score units).
    if correct_count == n_items:
        target_score = n_items - 0.3
        status = "ALL_CORRECT"
    elif correct_count == 0:
        target_score = 0.3
        status = "ALL_INCORRECT"
    else:
        target_score = float(correct_count)
        status = "ESTIMATED"

    # Initial ability estimate from log-odds of target score
    proportion = target_score / n_items
    theta = math.log(proportion / (1.0 - proportion))
    mean_b = sum(b for b, _ in valid_items) / n_items
    theta += mean_b  # initial center

    # Newton-Raphson iteration
    for _ in range(max_iter):
        score_diff = 0.0
        info_sum = 0.0

        for b, _ in valid_items:
            p = rasch_probability(theta, b)
            score_diff += (p)
            info_sum += (p * (1.0 - p))

        # f(theta) = sum(P_i) - target_score
        f_theta = score_diff - target_score

        if info_sum < 1e-9:
            break

        step = -f_theta / info_sum
        # Damping step to prevent divergence
        step = max(-1.0, min(1.0, step))
        theta += step
        theta = max(-4.0, min(4.0, theta))

        if abs(step) < tol:
            break

    # Standard error: SE(theta) = 1 / sqrt(I(theta))
    final_info = sum(rasch_information(theta, b) for b, _ in valid_items)
    se = (1.0 / math.sqrt(final_info)) if final_info > 1e-6 else None

    return {
        "theta": round(theta, 4),
        "std_error": round(se, 4) if se is not None else None,
        "status": status,
        "responses_count": n_items,
        "correct_count": correct_count,
    }
