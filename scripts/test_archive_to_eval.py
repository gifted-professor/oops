#!/usr/bin/env python3
import json
import os
import pathlib
import tempfile
import unittest
import wave

import archive_to_eval
import voiceprint_split


def archived(folder, seconds=3, end_ms=2500, complete=True):
    session = os.path.join(folder, "recordings", "11111111-aaaa-bbbb-cccc-222222222222")
    os.makedirs(session)
    with open(os.path.join(session, "audio.pcm"), "wb") as f:
        f.write(b"\x01\x00" * (16000 * seconds))
    rows = [{"id": "u1", "start_ms": 0, "end_ms": 1200, "text": "我来做。",
             "words": [{"text": "我来做。", "start_ms": 0, "end_ms": 1200}]},
            {"id": "u2", "start_ms": 1200, "end_ms": end_ms, "text": "好的。", "words": []},
            {"id": "u3", "start_ms": 2600, "end_ms": 2700, "text": "  ", "words": []}]
    with open(os.path.join(session, "finals.jsonl"), "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    if complete:
        with open(os.path.join(session, "complete.json"), "w") as f:
            f.write("{}")
    return session


class ArchiveToEvalTest(unittest.TestCase):
    def test_outputs_match_what_the_offline_tools_read(self):
        with tempfile.TemporaryDirectory() as root:
            session = archived(root)
            out = archive_to_eval.convert(session, "meeting-development", root)
            with wave.open(out["wav"]) as w:
                self.assertEqual((16000, 1, 2, 48000), (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()))
            self.assertEqual(2, out["utterances"])  # the blank sentence is dropped
            finals = json.load(open(out["finals"], encoding="utf-8"))
            self.assertEqual((0, 1200, "我来做。"), (finals[0]["start_time"], finals[0]["end_time"], finals[0]["text"]))
            self.assertEqual(1200, finals[0]["words"][0]["end_time"])
            summary = json.load(open(os.path.join(os.path.dirname(out["finals"]), "summary.json")))
            self.assertEqual((True, out["recording"]), (summary["complete"], summary["recording"]))
            wav = os.path.join(root, "test-media", out["recording"])
            self.assertEqual("meeting-development", voiceprint_split.recording_purpose(pathlib.Path(wav)))
            self.assertEqual("meeting-development", voiceprint_split.validate_recording_purpose(pathlib.Path(wav), "dev", "meetings"))
            with self.assertRaises(ValueError):
                voiceprint_split.validate_recording_purpose(pathlib.Path(wav), "holdout", "meetings")
            self.assertEqual(["u1", "u2"], [u["id"] for u in json.load(open(out["meeting"]))["utterances"]])

    def test_refuses_incomplete_mismatched_and_repurposed_recordings(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                archive_to_eval.convert(archived(root, complete=False), "meeting-development", root)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                archive_to_eval.convert(archived(root, seconds=2, end_ms=5000), "meeting-development", root)
        with tempfile.TemporaryDirectory() as root:
            session = archived(root)
            archive_to_eval.convert(session, "meeting-holdout", root)
            with self.assertRaises(ValueError):
                archive_to_eval.convert(session, "meeting-development", root)
            with self.assertRaises(ValueError):
                archive_to_eval.convert(session, "anything-else", root)


if __name__ == "__main__":
    unittest.main()
