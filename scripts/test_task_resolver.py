"""Verification guards for context-resolved tasks; no network."""
import unittest

from task_resolver import grade, owner_for, quote_found, verify

TRANSCRIPT = [{"id": "u53", "text": "先先做吉果的那个。吉果加豆包。"},
              {"id": "u54", "text": "诶，那你把那个顺便发我吧，我感觉我可以一起。跑一下那个，你说运营那个。"}]


class ResolverTest(unittest.TestCase):
    def raw(self, **changes):
        return {"is_task": True, "what": "把运营选题方案发给对方", "owner_role": "addressee", "owner_name": None,
                "due_raw": None, "missing": [],
                "resolutions": [{"phrase": "那个", "meaning": "吉果加豆包的运营方案",
                                 "evidence": [{"id": "u53", "quote": "吉果加豆包"}]}], **changes}

    def test_verified_resolution_and_two_person_addressee(self):
        result = verify(self.raw(), TRANSCRIPT, "u54", "那你把那个顺便发我吧")
        self.assertEqual([], result["missing"])
        owner = owner_for(result, "洪玉麟", ["林春源", "洪玉麟"])
        self.assertEqual("林春源", owner)
        self.assertEqual("complete", grade(result, owner))

    def test_invented_quote_becomes_missing_not_a_guess(self):
        raw = self.raw(resolutions=[{"phrase": "那个", "meaning": "季度预算表",
                                     "evidence": [{"id": "u53", "quote": "季度预算表格"}]}])
        result = verify(raw, TRANSCRIPT, "u54", "那你把那个顺便发我吧")
        self.assertEqual([], result["resolutions"])
        self.assertTrue(result["missing"])
        self.assertEqual("needs_info", grade(result, "林春源"))

    def test_owner_needs_speaker_evidence_and_known_names(self):
        result = verify(self.raw(owner_role="named", owner_name="张三"), TRANSCRIPT, "u54", "发我吧")
        self.assertEqual("unknown", result["owner_role"])
        self.assertIsNone(owner_for(verify(self.raw(), TRANSCRIPT, "u54", "发我吧"), None, ["林春源", "洪玉麟"]))
        self.assertIsNone(owner_for(verify(self.raw(), TRANSCRIPT, "u54", "发我吧"), "洪玉麟", ["林春源", "洪玉麟", "黄丽萍"]))

    def test_due_must_be_literal_and_short_quotes_rejected(self):
        result = verify(self.raw(due_raw="周五前"), TRANSCRIPT, "u54", "那你把那个顺便发我吧")
        self.assertIsNone(result["due_raw"])
        self.assertFalse(quote_found("吉果", TRANSCRIPT[0]["text"]))
        self.assertTrue(quote_found("吉果加豆包", TRANSCRIPT[0]["text"]))


if __name__ == "__main__":
    unittest.main()
