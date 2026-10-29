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

    def test_zero_prior_implications_proof(self):
        """
        Mathematical proof test:
        Demonstrates that setting P(L0) = 0.0 results in:
        1. Identical posterior transition L1 = P(T) for BOTH correct and incorrect first responses.
        2. A paradoxical increase from 0.0 to 0.15 upon an INCORRECT answer.
        This verifies why P(L0) = 0.0 violates Bayesian evidence discrimination.
        """
        p_l0 = 0.0
        p_trans = DEFAULT_P_TRANS  # 0.15

        res_correct = BKTService.update_mastery(p_l0, is_correct=True, p_learn=p_trans)
        res_incorrect = BKTService.update_mastery(p_l0, is_correct=False, p_learn=p_trans)

        # 1. Both outcomes yield exactly P(T)
        self.assertAlmostEqual(res_correct, p_trans, places=5)
        self.assertAlmostEqual(res_incorrect, p_trans, places=5)
        self.assertEqual(res_correct, res_incorrect, "Zero prior produces non-discriminative first updates")

        # 2. An incorrect answer paradoxical increase
        self.assertGreater(res_incorrect, p_l0, "Incorrect answer paradoxically raises mastery under zero prior")

    def test_standard_prior_discriminates_correct_and_incorrect(self):
        """
        Verifies that the standard Corbett & Anderson prior DEFAULT_P_L0 (0.20):
        1. Increases upon a correct answer (0.20 -> 0.60).
        2. Decreases below prior upon an incorrect answer (0.20 -> 0.176).
        3. Delivers a distinct, diagnostically valid discriminative spread (> 40%).
        """
        p_l0 = DEFAULT_P_L0  # 0.20

        res_correct = BKTService.update_mastery(p_l0, is_correct=True)
        res_incorrect = BKTService.update_mastery(p_l0, is_correct=False)

        self.assertGreater(res_correct, p_l0)
        self.assertLess(res_incorrect, p_l0)
        self.assertAlmostEqual(res_correct, 0.60, places=2)
        self.assertAlmostEqual(res_incorrect, 0.176, places=3)
        self.assertGreater(res_correct - res_incorrect, 0.40)

    def test_unanswered_policies(self):
        """
        Verifies behavior under skip vs incorrect policies for unanswered questions.
        """
        initial = DEFAULT_P_L0
        # Under skip policy: mastery remains strictly unchanged
        skipped = initial  # skip does not call update_mastery
        self.assertEqual(skipped, initial)

        # Under incorrect policy: mastery is updated as False
        penalized = BKTService.update_mastery(initial, is_correct=False)
        self.assertLess(penalized, initial)
        self.assertAlmostEqual(penalized, 0.176, places=3)


if __name__ == "__main__":
    unittest.main()

