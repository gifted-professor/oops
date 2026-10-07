"""Provisional splitting must not invent text or identities in overlapping speech."""
import base64
import types
import unittest
from local_backend import Backend, ApiError, align_words, resolve_owner
from speaker_preview import powerset_labels, smooth_labels, frame_runs, text_segments, identify_runs, SpeakerPreview


class PreviewTest(unittest.TestCase):
    def test_powerset_preserves_sustained_overlap_but_removes_blips(self):
        self.assertEqual([(), (0,), (1,), (2,), (0, 1), (0, 2), (1, 2)], powerset_labels(3, 2))
        labels = [(0,)] * 10 + [(0, 1)] * 15 + [(1,)] * 10
        runs = frame_runs(smooth_labels(labels), 1000, 991, 270)
        self.assertEqual([(0,), (0, 1), (1,)], [r[2] for r in runs])
        self.assertEqual((0, 1000), (runs[0][0], runs[-1][1]))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(runs, runs[1:])))
        self.assertEqual([(0,)] * 17, smooth_labels([(0,)] * 8 + [(0, 1)] + [(0,)] * 8))

    def test_padding_never_extends_recorded_audio(self):
        self.assertEqual([(0, 1000, (0,))], frame_runs([(0,)] * 100 + [()] * 489, 1000, 991, 270))

    def test_local_slot_never_spreads_one_name_across_disconnected_turns(self):
        calls = []
        def diagnose(pcm):
            calls.append(len(pcm))
            return {'speaker': '林春源' if pcm[0] == 1 else '洪玉麟'}
        pcm = bytes([1]) * 64000 + bytes(64000) + bytes([2]) * 64000
        result = identify_runs(pcm, [(0, 2000, (0,)), (2000, 4000, (0, 1)), (4000, 6000, (0,))], 0, diagnose)
        self.assertEqual(['林春源', None, '洪玉麟'], [r[3].get('speaker') for r in result])
        self.assertEqual([64000, 64000], calls)
        calls.clear()
        result = identify_runs(bytes([1]) * 320000, [(0, 10000, (0,))], 0, diagnose)
        self.assertEqual([80000] * 4, calls)

    def test_overlap_crossing_a_word_cannot_get_a_name(self):
        units, approx, _ = align_words('甲乙丙丁', [
            {'text': '甲乙', 'start_ms': 1000, 'end_ms': 1900},
            {'text': '丙丁', 'start_ms': 1900, 'end_ms': 2900}], 1000, 3000)
        activity = [{'start_ms': 1000, 'end_ms': 1800, 'kind': 'speech', 'speaker': '林春源', 'score': .9},
                    {'start_ms': 1800, 'end_ms': 2200, 'kind': 'overlap', 'speaker': None, 'score': 0},
                    {'start_ms': 2200, 'end_ms': 3000, 'kind': 'speech', 'speaker': '洪玉麟', 'score': .9}]
        result = text_segments('甲乙丙丁', units, activity, 1000, 3000, approx)
        self.assertEqual('甲乙丙丁', ''.join(s['text'] for s in result))
        self.assertTrue(all(s['speaker'] is None and s['kind'] == 'overlap' for s in result))

    def test_turns_preserve_asr_words_and_cover_the_clip(self):
        units, approx, _ = align_words('你好。好的！', [
            {'text': '你好', 'start_ms': 1100, 'end_ms': 1900},
            {'text': '好的', 'start_ms': 2200, 'end_ms': 2900}], 1000, 3000)
        result = text_segments('你好。好的！', units, [
            {'start_ms': 1000, 'end_ms': 2100, 'kind': 'speech', 'speaker': '林春源', 'score': .8},
            {'start_ms': 2100, 'end_ms': 3000, 'kind': 'speech', 'speaker': '洪玉麟', 'score': .9}], 1000, 3000, approx)
        self.assertEqual(['林春源', '洪玉麟'], [s['speaker'] for s in result])
        self.assertEqual(['你好。', '好的！'], [s['text'] for s in result])
        self.assertEqual(result[0]['end_ms'], result[1]['start_ms'])
        self.assertEqual((1000, 3000), (result[0]['start_ms'], result[-1]['end_ms']))
        whole = text_segments('不能均分这些字', [], [], 0, 9000, True)
        self.assertEqual(1, len(whole)); self.assertIsNone(whole[0]['speaker']); self.assertTrue(whole[0]['approx'])

    def test_jev_failure_and_overlap_remain_unknown(self):
        preview = SpeakerPreview.__new__(SpeakerPreview)
        activity = {'start_ms': 0, 'end_ms': 2000, 'kind': 'speech', 'speaker': None, 'score': .45,
                    '_slot': (0, (0,)), '_scores': [{'name': '林春源', 'score': .45}, {'name': '洪玉麟', 'score': .2}]}
        preview.activities = lambda *a: [dict(activity)]
        words = [{'text': '你好', 'start_ms': 0, 'end_ms': 2000}]
        class Fail:
            def choices(self, *a, **k): raise TimeoutError()
        result = preview.analyze(bytes(64000), 0, '你好', words, align_words, jev=Fail())
        self.assertIsNone(result['segments'][0]['speaker']); self.assertFalse(result['jev_used'])
        class Choose:
            def choices(self, *a, **k): return {'s0': {'choice': 'candidate', 'probabilities': {'candidate': .99}}}, 'jev-test'
        result = preview.analyze(bytes(64000), 0, '你好', words, align_words, jev=Choose())
        self.assertEqual('林春源', result['segments'][0]['speaker'])
        self.assertIsNone(resolve_owner({'utterances': [{'text': '我来做', 'preview': result}]}, self_enabled=True))
        activity['kind'] = 'overlap'
        result = preview.analyze(bytes(64000), 0, '你好', words, align_words, jev=Choose())
        self.assertIsNone(result['segments'][0]['speaker']); self.assertFalse(result['jev_used'])

    def test_endpoint_checks_audio_before_inference(self):
        backend = Backend.__new__(Backend)
        backend.speaker_preview = types.SimpleNamespace(analyze=lambda *a: {'ok': True})
        backend.jev = None
        valid = {'sample_rate': 16000, 'pcm_base64': base64.b64encode(bytes(96000)).decode(), 'clip_start_ms': 0}
        self.assertEqual({'ok': True}, backend.preview_speakers(valid))
        for bad in ({'clip_start_ms': -1}, {'clip_start_ms': True}, {'sample_rate': 48000},
                    {'pcm_base64': 'invalid'}, {'pcm_base64': base64.b64encode(bytes(32000)).decode()}):
            with self.assertRaises(ApiError): backend.preview_speakers(valid | bad)
        backend.speaker_preview = None
        with self.assertRaises(ApiError) as error: backend.preview_speakers(valid)
        self.assertEqual(501, error.exception.status)


if __name__ == '__main__': unittest.main()
