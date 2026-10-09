#!/usr/bin/env python3
"""In-app review → task_eval truth, marked non-blind and candidates-only."""
import unittest

from review_truth import review_truth


class ReviewTruthTest(unittest.TestCase):
    def test_reviews_become_truth_with_limits(self):
        meeting = {"session_id": "s1", "utterances": [
            {"id": "u1", "text": "我明天把方案发给大家", "tag": "commitment"},
            {"id": "u2", "text": "那你周五前整理一下预算", "tag": "commitment"},
            {"id": "u3", "text": "我们就定下周上线", "tag": "decision"},
            {"id": "u4", "text": "随便聊聊", "tag": "chitchat"},
            {"id": "u5", "text": "周末一起吃饭吧", "tag": "commitment"}],
            "reviews": {"u1": {"verdict": "confirmed", "owner": "洪玉麟", "speaker": "洪玉麟"},
                        "u2": {"verdict": "confirmed"},
                        "u3": {"verdict": "rejected", "speaker": "multi"},
                        "u4": {"verdict": "confirmed"},  # not a commitment: no task truth from it
                        "u5": {"verdict": "rejected"},
                        "gone": {"speaker": "林春源"}}}
        truth = review_truth(meeting)
        self.assertFalse(truth["blind"])
        self.assertEqual("model_candidates_only", truth["scope"])
        self.assertEqual([("u1", "洪玉麟", None), ("u2", None, "不确定")],
                         [(t["utterance"], t["owner"], t.get("note")) for t in truth["tasks"]])
        self.assertEqual(["u5"], [n["utterance"] for n in truth["non_tasks"]])
        self.assertEqual({"u1": "洪玉麟", "u3": "multi"}, truth["speakers"])
        self.assertEqual({"confirmed": [], "rejected": ["u3"]}, truth["decisions"])


if __name__ == "__main__":
    unittest.main()
