#!/usr/bin/env python3
"""Local recording and text-only segment sync contracts; no mic/cloud/Feishu."""
import datetime as dt
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import wave

from mac_audio_bridge import AUDIO, FRAME_BYTES, Session
from meeting_features import Meetings, candidates


class Connection:
    def __init__(self): self.sent, self.fail = [], False
    def settimeout(self, value): pass
    def sendall(self, data):
        if self.fail: raise OSError('disconnected')
        self.sent.append(data)
    def close(self): pass


class RecordingTest(unittest.TestCase):
    def test_opt_in_successful_frames_and_separate_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            plain = Session(Connection(), 'test', 'mic')
            plain.try_send(AUDIO, bytes(FRAME_BYTES)); plain.close()
            self.assertEqual([], list(Path(folder).iterdir()))
            conn = Connection()
            first = Session(conn, 'test', 'mic', folder)
            first.try_send(AUDIO, bytes(FRAME_BYTES))
            conn.fail = True
            self.assertFalse(first.try_send(AUDIO, bytes(FRAME_BYTES)))
            second = Session(Connection(), 'test', 'mic', folder)
            second.try_send(AUDIO, b'\x01\x00' * (FRAME_BYTES // 2)); second.close(); second.close()
            paths = list(Path(folder).glob('*.wav'))
            self.assertEqual(2, len(paths))
            for path in paths:
                with wave.open(str(path)) as audio:
                    self.assertEqual((16000, 1, 2, FRAME_BYTES // 2),
                                     (audio.getframerate(), audio.getnchannels(), audio.getsampwidth(), audio.getnframes()))
                meta = json.loads(path.with_suffix('.json').read_text())
                self.assertEqual(1, meta['frames'])
                self.assertTrue(meta['finalized'])
                self.assertFalse(meta['receiver_consumption_verified'])
                self.assertFalse(meta['audio_uploaded_by_recorder'])
                self.assertEqual(0o600, stat.S_IMODE(os.stat(path).st_mode))
                self.assertEqual(0o600, stat.S_IMODE(os.stat(path.with_suffix('.json')).st_mode))


def example():
    segment = dict(start_ms=0, end_ms=1000, text='我来做。', speaker='林春源', score=.7,
                   confidence=.9, tier='B', overlap=False, source='voiceprint', approx=False,
                   candidates=[dict(name='林春源', score=.7), dict(name='江古月', score=.5)], pcm='drop')
    return dict(id='u1', text='我来做。', start_ms=0, end_ms=1000, speaker='林春源',
                tag='commitment', segments=[segment], pcm_base64='drop', preview={'private':'drop'})


class SegmentSyncTest(unittest.TestCase):
    def test_persistence_roundtrip_keeps_fields_but_discards_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Meetings(folder)
            body = dict(session_id='test', started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        utterances=[example()])
            store.sync(body)
            restored = Meetings(folder).load('test')['utterances'][0]
            self.assertEqual(.9, restored['segments'][0]['confidence'])
            self.assertEqual('B', restored['segments'][0]['tier'])
            self.assertNotIn('pcm', restored['segments'][0])
            self.assertNotIn('pcm_base64', restored)
            self.assertNotIn('preview', restored)

    def test_alignment_semantics_finite_numbers_and_limits(self):
        for edit in (dict(text='漏字'), dict(start_ms=1), dict(end_ms=1001),
                     dict(confidence=float('nan')), dict(score=True),
                     dict(overlap=True), dict(tier='A', confidence=None),
                     dict(tier='A', approx=True), dict(source='invented'),
                     dict(candidates=[dict(name='林春源', score=.7)] * 2)):
            row = example(); row['segments'][0].update(edit)
            with self.subTest(edit=edit), self.assertRaises(ValueError): candidates([row])
        row = example(); row['segments'] *= 257
        with self.assertRaises(ValueError): candidates([row])
        row = example(); row['segments'][0].update(speaker=None, tier='C', confidence=None, overlap=True)
        self.assertTrue(candidates([row])[0]['segments'][0]['overlap'])


if __name__ == '__main__': unittest.main()
