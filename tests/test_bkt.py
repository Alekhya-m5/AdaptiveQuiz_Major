"""
Tests for Bayesian Knowledge Tracing (BKT) Posterior Updates and Concept Independence.
"""

import unittest
from app.bkt.services import BKTService, DEFAULT_P_L0, DEFAULT_P_TRANS, DEFAULT_P_GUESS, DEFAULT_P_SLIP


class TestBKT(unittest.TestCase):

    def test_posterior_update_correct_increases_mastery(self):
        initial = 0.20
        # Correct answer observed
        updated = BKTService.update_mastery(
            mastery=initial,
            is_correct=True,
            p_learn=0.15,
            p_guess=0.20,
            p_slip=0.10,
        )
        self.assertGreater(updated, initial)
        # Expected calculation:
        # P(obs) = 0.20 * 0.90 + 0.80 * 0.20 = 0.34
        # Posterior = (0.20 * 0.90) / 0.34 = 0.18 / 0.34 = 0.52941
        # L_next = 0.52941 + (1 - 0.52941) * 0.15 = 0.60000
        self.assertAlmostEqual(updated, 0.60, places=3)

    def test_posterior_update_incorrect_decreases_mastery(self):
        initial = 0.50
        # Incorrect answer observed
        updated = BKTService.update_mastery(
            mastery=initial,
            is_correct=False,
            p_learn=0.15,
            p_guess=0.20,
            p_slip=0.10,
        )
        self.assertLess(updated, initial)

    def test_sequence_of_successive_correct_answers(self):
        # A sequence of correct answers should approach 1.0 (mastery)
        mastery = DEFAULT_P_L0
        for _ in range(5):
            mastery = BKTService.update_mastery(mastery, is_correct=True)

        self.assertGreater(mastery, 0.90)
        self.assertLessEqual(mastery, 1.0)

    def test_independent_concept_updates(self):
        # Student answers correctly on Concept A, but incorrectly on Concept B
        mastery_a = DEFAULT_P_L0
        mastery_b = DEFAULT_P_L0

        mastery_a = BKTService.update_mastery(mastery_a, is_correct=True)
        mastery_b = BKTService.update_mastery(mastery_b, is_correct=False)

        self.assertGreater(mastery_a, DEFAULT_P_L0)
        self.assertLess(mastery_b, DEFAULT_P_L0)
        self.assertNotEqual(mastery_a, mastery_b)

    def test_deterministic_replay_idempotency(self):
        # Processing a sequence [True, False, True] twice from P(L0) yields identical mastery
        obs = [True, False, True, True]

        m1 = DEFAULT_P_L0
        for o in obs:
            m1 = BKTService.update_mastery(m1, is_correct=o)

        m2 = DEFAULT_P_L0
        for o in obs:
            m2 = BKTService.update_mastery(m2, is_correct=o)

        self.assertEqual(m1, m2)


if __name__ == "__main__":
    unittest.main()
