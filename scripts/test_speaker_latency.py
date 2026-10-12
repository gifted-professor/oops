#!/usr/bin/env python3
"""Deferred split: the whole-sentence answer never waits for it, retries hit a cache, short clips skip it."""
import base64
import threading
import unittest

import local_backend
from local_backend import ApiError, Backend, SplitJobs

SEGMENTS = [{"start_ms": 0, "end_ms": 2000, "text": "甲", "speaker": "林春源", "overlap": False},
            {"start_ms": 2000, "end_ms": 4000, "text": "乙", "speaker": None, "overlap": False}]


class FakeSpeakers:
    ready, names, generation = True, ["林春源", "洪玉麟"], 1

    def __init__(self):
        self.identified, self.gate = 0, None

    def identify(self, pcm):
        self.identified += 1
        return "林春源", .8, .3, [{"name": "林春源", "score": .8}, {"name": "洪玉麟", "score": .3}]

    def split(self, *args):
        if self.gate:
            self.gate.wait(5)
        return [dict(s) for s in SEGMENTS], 1.0


def request(seconds=4, **extra):
    return {"session_id": "s1", "utterance_id": "u1", "sample_rate": 16000, "text": "甲乙", "clip_start_ms": 0,
            "words": [], "pcm_base64": base64.b64encode(bytes(seconds * 32000)).decode(), **extra}


class DeferredSplitTest(unittest.TestCase):
    def setUp(self):
        self.saved = local_backend.consolidate_trial, local_backend.merge_for_display
        local_backend.consolidate_trial = lambda segments, *a: segments
        local_backend.merge_for_display = lambda segments: segments
        b = self.backend = Backend.__new__(Backend)
        b.speakers = FakeSpeakers()
        b.split_config, b.split_trial, b.split_min_ms = None, True, 3000
        b.speaker_state_lock = threading.RLock()
        b.SPLIT_WAIT_S = .3
        b.speaker_preview = None

    def tearDown(self):
        local_backend.consolidate_trial, local_backend.merge_for_display = self.saved

    def test_whole_sentence_returns_while_split_is_still_running(self):
        b = self.backend
        b.speakers.gate = threading.Event()
        result = b.speaker(request(split="deferred"))
        self.assertTrue(result["split_pending"])
        self.assertEqual("林春源", result["speaker"])
        self.assertNotIn("trial_segments", result)
        self.assertEqual("pending", b.speaker_split({"session_id": "s1", "utterance_id": "u1"})["status"])
        b.speakers.gate.set()
        b.SPLIT_WAIT_S = 5
        done = b.speaker_split({"session_id": "s1", "utterance_id": "u1"})
        self.assertEqual("done", done["status"])
        self.assertEqual(SEGMENTS, done["trial_segments"])

    def test_deferred_segments_equal_synchronous_segments(self):
        b = self.backend
        sync = b.speaker(request(utterance_id="sync"))
        b.SPLIT_WAIT_S = 5
        b.speaker(request(split="deferred"))
        late = b.speaker_split({"session_id": "s1", "utterance_id": "u1"})
        self.assertEqual(sync["trial_segments"], late["trial_segments"])
        self.assertIn("split_ms", sync["timing_ms"])

    def test_retry_is_served_from_cache(self):
        b = self.backend
        first = b.speaker(request(split="deferred"))
        again = b.speaker(request(split="deferred"))
        self.assertEqual(first["speaker"], again["speaker"])
        self.assertEqual(1, b.speakers.identified)
        b.speaker(request(split="deferred", utterance_id="u2"))
        self.assertEqual(2, b.speakers.identified)

    def test_short_clip_skips_split(self):
        b = self.backend
        for mode in ({}, {"split": "deferred"}):
            result = b.speaker(request(seconds=2, utterance_id="short" + str(len(mode)), **mode))
            self.assertNotIn("split_pending", result)
            self.assertNotIn("trial_segments", result)
            self.assertEqual("林春源", result["speaker"])

    def test_unknown_utterance_is_unavailable_and_bad_request_rejected(self):
        b = self.backend
        self.assertEqual("unavailable", b.speaker_split({"session_id": "x", "utterance_id": "nope"})["status"])
        with self.assertRaises(ApiError):
            b.speaker_split({"session_id": "x"})

    def test_failed_split_is_reported_unavailable(self):
        b = self.backend
        b.speakers.split = lambda *a: (_ for _ in ()).throw(RuntimeError("boom"))
        b.SPLIT_WAIT_S = 5
        b.speaker(request(split="deferred"))
        self.assertEqual("unavailable", b.speaker_split({"session_id": "s1", "utterance_id": "u1"})["status"])


class SplitJobsTest(unittest.TestCase):
    def test_backlog_beyond_limit_fails_fast(self):
        jobs, gate = SplitJobs(backlog=1), threading.Event()
        jobs.submit("a", lambda: gate.wait(5) or [1])
        jobs.submit("b", lambda: [2])
        self.assertEqual("failed", jobs.wait("b", 0)["state"])
        gate.set()
        self.assertEqual("done", jobs.wait("a", 5)["state"])

    def test_submit_is_idempotent_and_wait_times_out(self):
        jobs, gate = SplitJobs(), threading.Event()
        calls = []
        jobs.submit("k", lambda: calls.append(1) or gate.wait(5) or [])
        jobs.submit("k", lambda: calls.append(2) or [])
        self.assertEqual("pending", jobs.wait("k", .05)["state"])
        gate.set()
        self.assertEqual("done", jobs.wait("k", 5)["state"])
        self.assertEqual([1], calls)
        self.assertIsNone(jobs.wait("missing", 0))


if __name__ == "__main__":
    unittest.main()
