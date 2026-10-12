#!/usr/bin/env python3
import json
import os
import tempfile
import threading
import unittest
import urllib.request
import wave
from http.server import ThreadingHTTPServer

import label_meeting as lm
from task_eval import score

SID = "11111111-2222-3333-4444-555555555555"
U = [{"id": f"u{i}", "text": f"第{i}句", "start_ms": i * 2000, "end_ms": i * 2000 + 1500} for i in range(6)]


class LabelMeetingTest(unittest.TestCase):
    def test_truth_is_derived_in_the_format_task_eval_reads(self):
        labels = {"u0": {"speaker": "林春源", "task": "task", "owner": "洪玉麟", "dup": "A"},
                  "u1": {"speaker": "洪玉麟", "task": "task", "owner": "collective", "dup": "A"},
                  "u2": {"speaker": "multi", "task": "not_task"}, "u3": {"speaker": "江古月"}, "u4": {"dup": "B"}}
        truth = lm.derive_truth(SID, U, labels)
        self.assertEqual([("u0", "洪玉麟"), ("u1", None)], [(t["utterance"], t["owner"]) for t in truth["tasks"]])
        self.assertEqual("集体/大家", truth["tasks"][1]["note"])
        self.assertEqual([["u0", "u1"]], truth["duplicates"])  # a group of one is not a duplicate
        self.assertEqual(["u2"], [n["utterance"] for n in truth["non_tasks"]])
        self.assertEqual({"u0": "林春源", "u1": "洪玉麟", "u2": "multi", "u3": "江古月"}, truth["speakers"])
        result = score(truth, {"u0"})  # task_eval accepts it as is
        self.assertEqual((2, 1, 0), (result["positives"], result["tp"], result["fp"]))

    def test_unknown_ids_and_values_are_dropped(self):
        raw = {"u0": {"speaker": "路人甲", "task": "task", "owner": "林春源", "dup": "a"}, "zzz": {"task": "task"},
               "u1": {"task": "not_task", "owner": "林春源"}, "u2": "oops"}
        self.assertEqual({"u0": {"task": "task", "owner": "林春源"}, "u1": {"task": "not_task"}}, lm.clean_labels(raw, U))

    def test_clip_is_the_sentence_plus_padding_and_clamped(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "a.wav")
            with wave.open(path, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\x01\x00" * 16000 * 10)
            for start, end, expected_ms in ((2000, 3000, 1600), (0, 500, 800), (9500, 12000, 800)):
                data = lm.clip_wav(path, start, end)
                with wave.open(__import__("io").BytesIO(data)) as c:
                    self.assertEqual(expected_ms, c.getnframes() * 1000 // 16000, (start, end))

    def test_http_page_is_blind_saves_and_serves_audio(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "evidence", SID))
            os.makedirs(os.path.join(root, "test-media"))
            rows = [dict(u, speaker="林春源", tag="commitment") for u in U]  # model fields must never reach the page
            with open(os.path.join(root, "evidence", SID, "meeting.json"), "w", encoding="utf-8") as f:
                json.dump({"utterances": rows}, f, ensure_ascii=False)
            with wave.open(os.path.join(root, "test-media", f"{SID}-16k-mono.wav"), "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000 * 14)
            store = lm.Store(SID, root)
            server = ThreadingHTTPServer(("127.0.0.1", 0), lm.make_handler(store))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.shutdown)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            meeting = json.load(urllib.request.urlopen(base + "/api/meeting"))
            self.assertEqual(6, len(meeting["utterances"]))
            self.assertFalse({"speaker", "tag"} & set(meeting["utterances"][0]))
            body = json.dumps({"u0": {"speaker": "林春源", "task": "task", "owner": "洪玉麟"}}).encode()
            saved = json.load(urllib.request.urlopen(urllib.request.Request(base + "/api/labels", body, {"Content-Type": "application/json"})))
            self.assertEqual(1, saved["saved"])
            truth = json.load(open(os.path.join(root, "evidence", SID, "truth.json"), encoding="utf-8"))
            self.assertEqual("洪玉麟", truth["tasks"][0]["owner"])
            self.assertEqual(1, len(lm.Store(SID, root).labels))  # survives a restart
            self.assertEqual(b"RIFF", urllib.request.urlopen(base + "/clip/u1.wav").read()[:4])
            with self.assertRaises(Exception):
                urllib.request.urlopen(base + "/clip/nope.wav")


if __name__ == "__main__":
    unittest.main()
