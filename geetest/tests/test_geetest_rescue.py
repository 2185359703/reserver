"""Offline regression tests for the restricted, unverified rescue boundary."""

from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py"))
from geetest_rescue import geometry_rescue


def point(index, x, y=30, found=True):
    return {"index": index, "found": found, "center": [x, y],
            "box": [x - 5, y - 5, x + 5, y + 5],
            "verified": True, "candidate_verified": True}


def candidate(identifier, x, y=30):
    return {"id": identifier, "center": [x, y],
            "box": [x - 5, y - 5, x + 5, y + 5], "source": "color"}


class RescueTests(unittest.TestCase):
    def test_collision_can_be_recovered_without_claiming_verification(self):
        results = [point(1, 10), point(2, 12)]
        candidates = [candidate(3, 60), candidate(8, 120), candidate(12, 180)]
        changes = geometry_rescue(results, candidates, [[.9, .1], [.1, .85], [.2, .2]])
        self.assertEqual([item["center"] for item in results], [[60., 30.], [120., 30.]])
        self.assertEqual(len(changes), 2)
        self.assertEqual(results[0]["previous_center"], [10, 30])
        self.assertEqual(results[1]["previous_box"], [7, 25, 17, 35])
        for item in results:
            self.assertFalse(item["verified"])
            self.assertFalse(item["candidate_verified"])
            self.assertEqual(item["cv_source"], "cv_rescue")

    def test_noncolliding_model_points_are_immutable_even_with_large_score_gain(self):
        results = [point(1, 20), point(2, 80)]
        before = deepcopy(results)
        changes = geometry_rescue(results, [candidate(1, 140), candidate(2, 200)], [[.99, .1], [.1, .99]])
        self.assertEqual(changes, [])
        self.assertEqual(results, before)

    def test_missing_and_found_false_are_eligible(self):
        results = [{"index": 1, "found": False, "center": None, "box": None}, point(2, 80)]
        changes = geometry_rescue(results, [candidate(1, 140), candidate(2, 200)], [[.9, .1], [.1, .9]])
        self.assertEqual(len(changes), 1)
        self.assertEqual(results[0]["center"], [140., 30.])
        self.assertEqual(results[1], point(2, 80))
        self.assertIsNone(results[0]["previous_center"])
        found_false = [point(1, 25, found=False)]
        self.assertEqual(len(geometry_rescue(found_false, [candidate(1, 140), candidate(2, 200)], [[.9], [.1]])), 1)

    def test_low_margin_or_low_score_leaves_results_untouched(self):
        for scores in ([[.7, .4], [.68, .41]], [[.47, .4], [.1, .41]]):
            results = [point(1, 10), point(2, 11)]
            before = deepcopy(results)
            self.assertEqual(geometry_rescue(results, [candidate(1, 100), candidate(2, 160)], scores), [])
            self.assertEqual(results, before)

    def test_duplicate_proposals_do_not_create_false_low_margin(self):
        results = [{"found": False, "center": None}]
        candidates = [candidate(1, 100), candidate(2, 102), candidate(3, 180)]
        changes = geometry_rescue(results, candidates, [[.9], [.89], [.5]])
        self.assertEqual(len(changes), 1)
        self.assertAlmostEqual(results[0]["cv_margin"], .4)

    def test_no_independent_competitor_is_insufficient_evidence(self):
        results = [{"found": False, "center": None}]
        self.assertEqual(geometry_rescue(results, [candidate(1, 100), candidate(2, 102)], [[.9], [.7]]), [])

    def test_conflict_with_retained_point_does_not_force_second_choice(self):
        results = [{"found": False, "center": None}, point(2, 100)]
        before = deepcopy(results)
        changes = geometry_rescue(results, [candidate(1, 105), candidate(2, 200)], [[.9, .1], [.8, .9]])
        self.assertEqual(changes, [])
        self.assertEqual(results, before)

    def test_colliding_proposals_are_both_revoked(self):
        results = [point(1, 10), point(2, 11)]
        before = deepcopy(results)
        changes = geometry_rescue(results, [candidate(1, 100), candidate(2, 200)], [[.9, .85], [.1, .1]])
        self.assertEqual(changes, [])
        self.assertEqual(results, before)

    def test_revocation_cascade_rechecks_newly_retained_points(self):
        results = [point(1, 10), point(2, 11), point(3, 100)]
        before = deepcopy(results)
        candidates = [candidate(1, 100), candidate(2, 25), candidate(3, 200)]
        # First proposal hits retained #3. Revoking it restores x=10, which
        # then also blocks the second proposal at x=25.
        changes = geometry_rescue(results, candidates, [[.9, .1, .1], [.1, .9, .1], [.1, .1, .9]])
        self.assertEqual(changes, [])
        self.assertEqual(results, before)

    def test_invalid_matrix_is_rejected_before_mutation(self):
        results = [point(1, 10), point(2, 11)]
        before = deepcopy(results)
        with self.assertRaises(ValueError):
            geometry_rescue(results, [candidate(1, 100)], [[.9]])
        self.assertEqual(results, before)


if __name__ == "__main__":
    unittest.main()
