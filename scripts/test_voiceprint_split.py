#!/usr/bin/env python3
"""Test independent truth handling and evaluation denominators without model predictions."""
import unittest

from voiceprint_split import validated_truth, metrics, gates
from asr_words_probe import summarize, difference_kind


class EvaluationTest(unittest.TestCase):
    def setUp(self):
        self.case = {"id": "u1", "text": "甲乙丙丁", "start_ms": 10000, "end_ms": 14000,
                     "boundaries": [{"char": 2, "time_ms": 11000}], "context": []}
        self.manifest = {"hash": "frozen", "cases": [self.case]}
        self.answer = {"cuts": [2], "segments": [{"speakers": ["洪玉麟"]}, {"speakers": ["林春源"]}],
                       "self_present": False, "self_owner": None, "reviewed": True}

    def test_truth_requires_real_labels_and_allowed_boundaries(self):
        body = {"manifest_hash": "frozen", "answers": {"u1": self.answer}}
        self.assertEqual(body, validated_truth(body, self.manifest))
        for change in ({"cuts": [1]}, {"segments": [{"speakers": []}, {"speakers": ["林春源"]}]},
                       {"self_present": True}, {"segments": [{"speakers": ["洪玉麟", "听不出"]}, {"speakers": ["林春源"]}]}):
            with self.assertRaises(ValueError):
                validated_truth({"manifest_hash": "frozen", "answers": {"u1": self.answer | change}}, self.manifest)

    def test_unknown_counts_as_incorrect_and_short_wrong_is_separate(self):
        predictions = {"u1": [{"text": "甲乙", "start_ms": 10000, "end_ms": 11000, "speaker": "林春源"},
                              {"text": "丙丁", "start_ms": 11000, "end_ms": 14000, "speaker": None}]}
        result = metrics([self.case], {"u1": self.answer}, predictions)
        self.assertEqual(0, result["char_accuracy"])
        self.assertEqual(1, result["wrong_claim_rate"])
        self.assertEqual(.5, result["unknown_rate"])
        self.assertEqual(1, result["short_count"])
        self.assertEqual(1, result["short_wrong_rate"])

    def test_overlap_is_separate_and_extra_changepoints_are_false(self):
        overlap = self.answer | {"segments": [{"speakers": ["洪玉麟", "林春源"]}, {"speakers": ["林春源"]}]}
        pred = {"u1": [{"text": "甲", "start_ms": 10000, "end_ms": 10500, "speaker": "洪玉麟"},
                       {"text": "乙", "start_ms": 10500, "end_ms": 11000, "speaker": "林春源"},
                       {"text": "丙", "start_ms": 11000, "end_ms": 12500, "speaker": "洪玉麟"},
                       {"text": "丁", "start_ms": 12500, "end_ms": 14000, "speaker": "林春源"}]}
        result = metrics([self.case], {"u1": overlap}, pred)
        self.assertEqual(2, result["chars"])
        self.assertEqual(2, result["overlap_chars"])
        self.assertEqual(1, result["overlap_any_speaker_accuracy"])
        self.assertEqual(0, result["change_count"])
        self.assertEqual(1, result["false_change_points"])

    def test_small_owner_sample_requires_zero_errors(self):
        baseline = {"char_accuracy": .5, "wrong_claim_rate": .04}
        result = {"char_accuracy": .7, "wrong_claim_rate": .04, "chars": 100,
                  "change_count": 3, "change_recall": 1, "short_count": 3, "short_recall": 1, "short_wrong_rate": 0,
                  "self_count": 20, "self_claimed": 20, "self_coverage": 1, "self_accuracy": .95, "self_wrong": 1}
        self.assertEqual((True, False), gates(result, baseline))
        self.assertEqual((True, True), gates(result | {"self_wrong": 0, "self_accuracy": 1}, baseline))
        self.assertEqual((False, False), gates(result | {"wrong_claim_rate": .051}, baseline))

    def test_probe_excludes_untimed_whitespace_but_rejects_invalid_spoken_times(self):
        u = {"start_time": 1000, "end_time": 2000, "text": "甲 乙。", "words": [
            {"text": "甲", "start_time": 1100, "end_time": 1300},
            {"text": " ", "start_time": -1, "end_time": -1},
            {"text": "乙", "start_time": 1500, "end_time": 1800}]}
        result = summarize([u])
        self.assertEqual(1, result["with_word_times"])
        self.assertEqual(.7, result["rows"][0]["word_span_fraction"])
        u["words"][2]["start_time"] = -1
        self.assertEqual(0, summarize([u])["with_word_times"])

    def test_probe_separates_numeric_candidates_and_revisions(self):
        self.assertEqual("itn_candidate", difference_kind("预算10万。", [{"text": "预算十万"}]))
        self.assertEqual("revision", difference_kind("他说好。", [{"text": "他说坏"}]))


if __name__ == "__main__":
    unittest.main()
