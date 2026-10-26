"""
Tests for Item Response Theory (Rasch / 1PL) Probability, Calibration, and Ability Estimation.
"""

import unittest
from app.irt.probability import rasch_probability, rasch_information, rasch_log_likelihood
from app.irt.calibration import calibrate_items, estimate_theta_mle


class TestIRT(unittest.TestCase):

    def test_probability_basic_properties(self):
        # When theta == difficulty, probability is exactly 0.5
        self.assertAlmostEqual(rasch_probability(0.0, 0.0), 0.5, places=5)
        self.assertAlmostEqual(rasch_probability(1.5, 1.5), 0.5, places=5)
        self.assertAlmostEqual(rasch_probability(-2.0, -2.0), 0.5, places=5)

        # Monotonicity: higher ability -> higher probability of correct answer
        p_low = rasch_probability(-1.0, 0.0)
        p_med = rasch_probability(0.0, 0.0)
        p_high = rasch_probability(1.0, 0.0)
        self.assertTrue(p_low < p_med < p_high)

        # Harder item -> lower probability
        self.assertTrue(rasch_probability(0.0, 1.0) < rasch_probability(0.0, -1.0))

    def test_probability_numerical_overflow_safeguards(self):
        # Extreme positive difference should not throw OverflowError
        p_extreme_high = rasch_probability(1000.0, 0.0)
        self.assertTrue(0.9999 < p_extreme_high <= 1.0)

        # Extreme negative difference should not underflow to negative
        p_extreme_low = rasch_probability(-1000.0, 0.0)
        self.assertTrue(0.0 <= p_extreme_low < 0.0001)

    def test_fisher_information(self):
        # Information is maximized at theta == b (P = 0.5, I = 0.25)
        info_peak = rasch_information(0.0, 0.0)
        info_away = rasch_information(2.0, 0.0)
        self.assertAlmostEqual(info_peak, 0.25, places=5)
        self.assertTrue(info_away < info_peak)

    def test_item_calibration_scale_anchoring(self):
        # Create a population of 20 students answering 3 items of differing difficulty
        responses = []
        # Item 1: Easy (18/20 correct)
        # Item 2: Medium (10/20 correct)
        # Item 3: Hard (4/20 correct)
        for s_idx in range(20):
            responses.append({"question_id": "I1", "is_correct": s_idx < 18, "concept_tag": "T1"})
            responses.append({"question_id": "I2", "is_correct": s_idx < 10, "concept_tag": "T1"})
            responses.append({"question_id": "I3", "is_correct": s_idx < 4, "concept_tag": "T1"})

        calibs = calibrate_items(responses, min_responses=5)

        self.assertEqual(len(calibs), 3)
        self.assertEqual(calibs["I1"]["calibration_status"], "CALIBRATED")
        self.assertEqual(calibs["I2"]["calibration_status"], "CALIBRATED")
        self.assertEqual(calibs["I3"]["calibration_status"], "CALIBRATED")

        b1 = calibs["I1"]["difficulty_b"]
        b2 = calibs["I2"]["difficulty_b"]
        b3 = calibs["I3"]["difficulty_b"]

        # Easy item difficulty < Medium < Hard
        self.assertTrue(b1 < b2 < b3)

        # Scale Identifiability: sum of calibrated difficulties must equal 0.0 logits (mean = 0)
        mean_b = (b1 + b2 + b3) / 3.0
        self.assertAlmostEqual(mean_b, 0.0, places=3)

    def test_item_calibration_edge_cases(self):
        # Item with 100% correct (ceiling) and 0% correct (floor)
        responses = []
        for s in range(10):
            responses.append({"question_id": "ALL_CORR", "is_correct": True})
            responses.append({"question_id": "ALL_INCORR", "is_correct": False})
            responses.append({"question_id": "NORMAL", "is_correct": s < 5})

        calibs = calibrate_items(responses, min_responses=5)
        self.assertEqual(calibs["ALL_CORR"]["calibration_status"], "ALL_CORRECT")
        self.assertEqual(calibs["ALL_INCORR"]["calibration_status"], "ALL_INCORRECT")
        self.assertEqual(calibs["NORMAL"]["calibration_status"], "CALIBRATED")

    def test_item_calibration_insufficient_data(self):
        # Only 2 responses for an item when min_responses is 5
        responses = [
            {"question_id": "FEW_RESP", "is_correct": True},
            {"question_id": "FEW_RESP", "is_correct": False},
        ]
        calibs = calibrate_items(responses, min_responses=5)
        self.assertEqual(calibs["FEW_RESP"]["calibration_status"], "INSUFFICIENT_DATA")
        self.assertEqual(calibs["FEW_RESP"]["difficulty_b"], 0.0)

    def test_student_theta_estimation_mle(self):
        difficulties = {"Q1": -1.0, "Q2": 0.0, "Q3": 1.0}

        # Student A: answered all 3 correctly
        resp_all = [{"question_id": "Q1", "is_correct": True},
                    {"question_id": "Q2", "is_correct": True},
                    {"question_id": "Q3", "is_correct": True}]
        est_all = estimate_theta_mle(resp_all, difficulties)
        self.assertEqual(est_all["status"], "ALL_CORRECT")
        self.assertTrue(est_all["theta"] > 1.5)

        # Student B: answered 2 of 3 correctly
        resp_med = [{"question_id": "Q1", "is_correct": True},
                    {"question_id": "Q2", "is_correct": True},
                    {"question_id": "Q3", "is_correct": False}]
        est_med = estimate_theta_mle(resp_med, difficulties)
        self.assertEqual(est_med["status"], "ESTIMATED")

        # Student C: answered 0 of 3 correctly
        resp_zero = [{"question_id": "Q1", "is_correct": False},
                     {"question_id": "Q2", "is_correct": False},
                     {"question_id": "Q3", "is_correct": False}]
        est_zero = estimate_theta_mle(resp_zero, difficulties)
        self.assertEqual(est_zero["status"], "ALL_INCORRECT")
        self.assertTrue(est_zero["theta"] < -1.5)

        # Monotonic ordering
        self.assertTrue(est_zero["theta"] < est_med["theta"] < est_all["theta"])

    def test_student_theta_insufficient_data(self):
        est = estimate_theta_mle([], {"Q1": 0.0}, min_items=1)
        self.assertEqual(est["status"], "INSUFFICIENT_DATA")
        self.assertEqual(est["theta"], 0.0)


if __name__ == "__main__":
    unittest.main()
