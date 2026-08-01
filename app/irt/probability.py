"""
Rasch (1PL) Probability Model

This module contains only mathematical functions.
It has no dependency on Flask or SQLAlchemy.
"""

import math


def rasch_probability(theta: float, difficulty: float) -> float:
    """
    Calculate the probability that a student answers
    a question correctly using the Rasch model.
        Probability between 0 and 1.
    """

    return 1 / (1 + math.exp(-(theta - difficulty)))