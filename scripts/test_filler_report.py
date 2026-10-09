#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

import filler_report


class FillerReportTest(unittest.TestCase):
    def test_counts_hidden_joined_and_short_sentences(self):
        rows = [("嗯。", 1), ("呃，", 1), ("嗯。", 1), ("好的。", 1), ("手机号，来。呃，178。嗯。1855。嗯。4702。", 7),
                ("100。200。300", 3), ("对", 1), ("我们下周再讨论这个方案。", 4)]
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "s.finals.jsonl")
            with open(path, "w", encoding="utf-8") as f:
                for i, (text, seconds) in enumerate(rows):
                    f.write(json.dumps({"id": f"u{i}", "text": text, "start_ms": 0, "end_ms": seconds * 1000}, ensure_ascii=False) + "\n")
            report = filler_report.review(filler_report.find([folder]), filler_report.filler_chars())
        self.assertEqual((8, 3, 3.0, 1), (report["sentences"], report["hidden"], report["hidden_seconds"], report["phone_numbers_joined"]))
        self.assertEqual([("嗯。", 2), ("呃，", 1)], report["hidden_forms"])
        shown = dict(report["short_still_shown"])
        self.assertEqual({"好的": 1, "对": 1}, {k: shown[k] for k in ("好的", "对")})

    def test_filler_list_is_read_from_the_app_source(self):
        chars = filler_report.filler_chars()
        self.assertTrue(set("嗯呃啊") <= set(chars))
        self.assertNotIn("对", chars)


if __name__ == "__main__":
    unittest.main()
