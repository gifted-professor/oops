"""Resumable, verified raw-asset upload on the server; no network."""
import hashlib
import os
import stat
import tempfile
import unittest

from recordings import RecordingError, Recordings


def sha(data):
    return hashlib.sha256(data).hexdigest()


class RecordingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.gone = set()
        self.store = Recordings(os.path.join(self.temp.name, "rec"), deleted=lambda sid: sid in self.gone)
        self.audio, self.finals = os.urandom(3000) + b"\0\0", b'{"id":"u1","text":"\xe5\xa5\xbd"}\n'
        self.files = [{"name": "audio.pcm", "size": len(self.audio), "sha256": sha(self.audio)},
                      {"name": "finals.jsonl", "size": len(self.finals), "sha256": sha(self.finals)}]

    def upload(self, sid, data, name, start=0, step=1000):
        for at in range(start, len(data), step):
            part = data[at:at + step]
            self.store.chunk(sid, name, at, part, sha(part))

    def test_resumable_upload_completes_only_after_whole_file_check(self):
        sid = "session-0001"
        self.assertEqual({"audio.pcm": 0, "finals.jsonl": 0}, self.store.manifest(sid, {"files": self.files})["received"])
        self.upload(sid, self.audio[:2000], "audio.pcm")
        with self.assertRaises(RecordingError) as wrong:
            self.store.chunk(sid, "audio.pcm", 0, self.audio[:10], sha(self.audio[:10]))
        self.assertEqual(409, wrong.exception.status)
        self.assertEqual(2000, wrong.exception.extra["received"])
        resumed = self.store.manifest(sid, {"files": self.files})["received"]  # e.g. after an app restart
        self.assertEqual(2000, resumed["audio.pcm"])
        with self.assertRaises(RecordingError):
            self.store.complete(sid)
        self.upload(sid, self.audio, "audio.pcm", start=2000)
        self.upload(sid, self.finals, "finals.jsonl")
        self.assertTrue(self.store.complete(sid)["complete"])
        self.assertTrue(self.store.manifest(sid, {"files": self.files})["complete"])
        folder = self.store.folder(sid)
        self.assertEqual(self.audio, open(os.path.join(folder, "audio.pcm"), "rb").read())
        self.assertEqual(0o700, stat.S_IMODE(os.stat(folder).st_mode))
        self.assertEqual(0o600, stat.S_IMODE(os.stat(os.path.join(folder, "audio.pcm")).st_mode))

    def test_timing_log_is_an_archived_file(self):
        sid, timing = "session-0009", b'{"id":"u1","final_lag_ms":420}\n'
        files = self.files + [{"name": "timing.jsonl", "size": len(timing), "sha256": sha(timing)}]
        self.assertIn("timing.jsonl", self.store.manifest(sid, {"files": files})["received"])
        self.upload(sid, self.audio, "audio.pcm")
        self.upload(sid, self.finals, "finals.jsonl")
        self.upload(sid, timing, "timing.jsonl")
        self.assertTrue(self.store.complete(sid)["complete"])
        self.assertEqual(timing, open(os.path.join(self.store.folder(sid), "timing.jsonl"), "rb").read())

    def test_bad_chunk_bad_file_and_grown_manifest(self):
        sid = "session-0002"
        self.store.manifest(sid, {"files": self.files})
        with self.assertRaises(RecordingError):
            self.store.chunk(sid, "audio.pcm", 0, b"abcd", sha(b"other"))
        tampered = bytes([self.audio[0] ^ 1]) + self.audio[1:]
        self.upload(sid, tampered, "audio.pcm")
        self.upload(sid, self.finals, "finals.jsonl")
        with self.assertRaises(RecordingError) as failed:
            self.store.complete(sid)
        self.assertEqual(0, failed.exception.extra["received"])  # corrupt file restarts from zero
        grown = self.finals + b'{"id":"u2"}\n'
        files = [self.files[0], {"name": "finals.jsonl", "size": len(grown), "sha256": sha(grown)}]
        self.assertEqual(len(self.finals), self.store.manifest(sid, {"files": files})["received"]["finals.jsonl"])

    def test_deleted_meeting_refuses_uploads_and_delete_removes_audio(self):
        sid = "session-0003"
        self.store.manifest(sid, {"files": self.files})
        self.store.delete(sid)
        self.assertFalse(os.path.exists(self.store.folder(sid)))
        self.gone.add(sid)
        with self.assertRaises(RecordingError) as refused:
            self.store.manifest(sid, {"files": self.files})
        self.assertEqual(410, refused.exception.status)
        for bad in ("../etc", "short", "a/b-cccccccc"):
            with self.assertRaises(RecordingError):
                self.store.folder(bad)
        with self.assertRaises(RecordingError):
            self.store.manifest("session-0004", {"files": [{"name": "../x", "size": 1, "sha256": "0" * 64}]})



    def test_clip_slices_archived_audio_as_wav(self):
        import io, wave
        sid = "session-clip"
        audio = bytes(range(256)) * 250  # 64000 bytes = 2 s
        files = [{"name": "audio.pcm", "size": len(audio), "sha256": sha(audio)}]
        with self.assertRaises(RecordingError) as missing:
            self.store.clip(sid, 0, 500)
        self.assertEqual(404, missing.exception.status)
        self.store.manifest(sid, {"files": files})
        self.upload(sid, audio[:32000], "audio.pcm", step=16000)
        with self.assertRaises(RecordingError) as partial:
            self.store.clip(sid, 900, 1500)  # not uploaded that far yet
        self.assertEqual(409, partial.exception.status)
        self.upload(sid, audio, "audio.pcm", start=32000, step=16000)
        with wave.open(io.BytesIO(self.store.clip(sid, 1000, 1500))) as clip:
            self.assertEqual((1, 2, 16000), (clip.getnchannels(), clip.getsampwidth(), clip.getframerate()))
            frames = clip.readframes(clip.getnframes())
        self.assertEqual(audio[700 * 32:1800 * 32], frames)  # 300 ms padding each side
        with wave.open(io.BytesIO(self.store.clip(sid, 0, 2000))) as clip:
            self.assertEqual(audio, clip.readframes(clip.getnframes()))  # padding clamps at both ends
        for start, end in ((500, 500), (-1, 100), (0, 30001), (0.5, 100)):
            with self.assertRaises(RecordingError):
                self.store.clip(sid, start, end)
        self.gone.add(sid)
        with self.assertRaises(RecordingError):
            self.store.clip(sid, 0, 500)


if __name__ == "__main__":
    unittest.main()
