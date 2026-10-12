#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

import meetings_overview

REAL = "22bec74d-f061-4978-8e8d-4b2959c1d0c8"


def utterances(n, named):
    return [{"id": f"u{i}", "end_ms": 60000 * (i + 1), "tag": "chitchat" if i % 2 else "commitment",
             "speaker": "林春源" if i < named else None} for i in range(n)]


class MeetingsOverviewTest(unittest.TestCase):
    def test_real_meetings_are_separated_from_smoke_records(self):
        with tempfile.TemporaryDirectory() as state:
            os.makedirs(os.path.join(state, "meetings"))
            os.makedirs(os.path.join(state, "recordings", REAL))
            open(os.path.join(state, "recordings", REAL, "complete.json"), "w").close()
            records = {REAL: utterances(10, 6), "smoke-rec-a9bfe801": utterances(1, 0),
                       "speed-deploy-20261009": utterances(2, 0), "11111111-1111-4111-8111-111111111111": utterances(3, 3)}
            for sid, items in records.items():
                with open(os.path.join(state, "meetings", sid + ".json"), "w") as f:
                    json.dump({"session_id": sid, "started_at": "2026-10-09T03:16:00Z", "utterances": items}, f)
            open(os.path.join(state, "meetings", "smoke-x.json.deleted"), "w").close()
            rows = {r["session_id"]: r for r in (meetings_overview.summarise(m, state) for m in meetings_overview.load(state))}
        self.assertEqual(4, len(rows))
        self.assertTrue(rows[REAL]["real"])
        self.assertEqual((10, 10.0, 0.6, True), (rows[REAL]["utterances"], rows[REAL]["minutes"], rows[REAL]["named_share"], rows[REAL]["has_recording"]))
        self.assertEqual({"commitment": 5, "chitchat": 5}, rows[REAL]["tags"])
        self.assertFalse(rows["smoke-rec-a9bfe801"]["real"])
        self.assertFalse(rows["11111111-1111-4111-8111-111111111111"]["real"])  # a UUID but only 3 sentences

    def test_missing_folder_is_reported(self):
        with tempfile.TemporaryDirectory() as state:
            self.assertEqual(1, meetings_overview.main([state]))


if __name__ == "__main__":
    unittest.main()
