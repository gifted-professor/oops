#!/usr/bin/env python3
import unittest

import local_backend
from task_eval import score, stability

TRUTH = {"tasks": [{"utterance": "u1"}, {"utterance": "u2"}, {"utterance": "u78"}],
         "non_tasks": [{"utterance": "u9"}], "duplicates": [["u77", "u78"]]}


class TaskEvalTest(unittest.TestCase):
    def test_duplicate_group_is_one_task_and_extra_members_are_not_false_alarms(self):
        both = score(TRUTH, {"u1", "u77", "u78"})
        self.assertEqual((4, 3, 0, 1), (both["positives"], both["tp"], both["fp"], both["fn"]))
        self.assertEqual((3, 2), (both["tasks"], both["tasks_found"]))  # u1, u2, {u77,u78}
        self.assertEqual(1, both["duplicates_flagged"])
        self.assertEqual(["u2"], both["missed"])
        one = score(TRUTH, {"u1", "u2", "u77"})
        self.assertEqual((3, 3), (one["tasks"], one["tasks_found"]))
        self.assertEqual(0, one["duplicates_flagged"])

    def test_false_positives_hard_negatives_and_empty_run(self):
        r = score(TRUTH, {"u1", "u9", "u50"})
        self.assertEqual((1, 2), (r["tp"], r["fp"]))
        self.assertEqual(["u50", "u9"], r["false_positives"])
        self.assertEqual(["u9"], r["hard_negatives_flagged"])
        self.assertEqual(0.333, r["precision"])
        empty = score(TRUTH, set())
        self.assertEqual((0.0, 1.0), (empty["recall"], empty["precision"]))

    def test_stability_lists_only_inconsistent_calls(self):
        s = stability(TRUTH, [{"u1", "u2"}, {"u1"}, {"u1", "u2", "u5"}])
        self.assertEqual(["u1"], s["flagged_in_every_run"])
        self.assertEqual({"u2": 2, "u5": 1}, s["unstable"])

    def test_clause_floor_is_shared_with_the_live_tagger(self):
        clauses = [("甲", .72), ("乙", .95), ("丙", .5)]
        self.assertEqual(("乙", .95), local_backend.pick_task_clause(clauses, "idea"))
        self.assertIsNone(local_backend.pick_task_clause([("甲", .85)], "decision"))
        self.assertEqual(("甲", .91), local_backend.pick_task_clause([("甲", .91)], "decision"))
        self.assertIsNone(local_backend.pick_task_clause([], "idea"))


if __name__ == "__main__":
    unittest.main()
