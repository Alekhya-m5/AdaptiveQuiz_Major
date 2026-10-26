"""
Rasch (1PL) Probability Model.

Mathematical probability calculations with numerical stability safeguards.
Independent of Flask or SQLAlchemy.
"""

import math

# Clamping bounds to prevent floating-point overflow in exp(-z)
Z_MIN = -35.0
Z_MAX = 35.0


def rasch_probability(theta: float, difficulty: float) -> float:
    """
    Calculate the probability that a learner with ability `theta` answers
    an item of difficulty `difficulty` (parameter b) correctly under the Rasch (1PL) model:

        P(correct | theta, b) = 1 / (1 + exp(-(theta - b)))

    Parameters
    ----------
    theta : float
        Learner ability parameter.
    difficulty : float
        Item difficulty parameter b.

    Returns
    -------
    float
        Probability of correct answer, strictly bounded in (0, 1).
    """
    diff = theta - difficulty
    # Clip diff to prevent overflow in math.exp
    if diff > Z_MAX:
        return 1.0 - 1e-15
    if diff < Z_MIN:
        return 1e-15

    return 1.0 / (1.0 + math.exp(-diff))


def rasch_information(theta: float, difficulty: float) -> float:
    """
    Fisher Information of an item at ability level `theta`:
        I(theta, b) = P(theta, b) * (1 - P(theta, b))
    """
    p = rasch_probability(theta, difficulty)
    return p * (1.0 - p)


def rasch_log_likelihood(theta: float, difficulties: list, responses: list) -> float:
    """
    Compute log-likelihood for a response vector given theta and difficulties.
    responses: list of binary outcomes (0 or 1).
    """
    ll = 0.0
    for b, u in zip(difficulties, responses):
        p = rasch_probability(theta, b)
        if u == 1:
            ll += math.log(max(p, 1e-15))
        else:
            ll += math.log(max(1.0 - p, 1e-15))
    return ll