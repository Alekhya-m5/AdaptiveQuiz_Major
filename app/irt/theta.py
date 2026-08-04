"""
Theta update logic for Rasch (1PL).

This module updates the student's ability (theta)
after every answered question.
"""

from app.irt.probability import rasch_probability

# Learning rate
LEARNING_RATE = 0.10
weight=0


def update_theta(theta: float,
                 difficulty: float,
                 is_correct: bool) -> float:
    """
    Update student's theta using one response.

    Parameters
    ----------
    theta : float
        Current student ability.

    difficulty : float
        Question difficulty.

    is_correct : bool
        True if student answered correctly.

    Returns
    -------
    float
        Updated theta.
    """

    # Expected probability
    expected = rasch_probability(theta, difficulty)

    # Actual outcome
    actual = 1 if is_correct else 0
    if difficulty == -1:      # Easy
        weight = 0.8
    elif difficulty == 0:     # Medium
        weight = 1.0
    else:                     # Hard
        weight = 1.2

    new_theta = theta + LEARNING_RATE * weight * (actual - expected)

    return new_theta