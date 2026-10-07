import json
import os
import struct
import tempfile
import unittest
from unittest.mock import patch
from voiceprint_registry import VoiceprintRegistry, audio_quality


class RegistryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "private", "voiceprints.json")
        self.seed = os.path.join(self.temp.name, "seed.json")
        with open(self.seed, "w") as f:
            json.dump({"people": {"甲": {"vector": [1, 0], "seconds": 30}, "乙": {"vector": [0, 1], "seconds": 30}}}, f)
        self.registry = VoiceprintRegistry(self.path, self.seed, ["甲", "乙"])

    def test_replace_only_selected_person_retry_and_restart(self):
        revision = self.registry.revision()
        result = self.registry.save("甲", [3, 4], {"seconds": 30}, "mic", "sample-123", revision)
        self.assertNotIn("vector", json.dumps(result))
        self.assertEqual([0, 1], self.registry.data["people"]["乙"]["vector"])
        self.assertEqual(result, self.registry.save("甲", [3, 4], {"seconds": 30}, "mic", "sample-123", revision))
        reloaded = VoiceprintRegistry(self.path, self.seed, ["甲", "乙"])
        self.assertEqual(result, reloaded.catalog())
        self.assertEqual(0o600, os.stat(self.path).st_mode & 0o777)

    def test_stale_revision_and_invalid_vector_do_not_modify_profile(self):
        original = self.registry.catalog()
        for vector, revision in (([1, 0], "outdated"), ([float('nan'), 1], original['revision']), ([0, 0], original['revision'])):
            with self.assertRaises(ValueError):
                self.registry.save("甲", vector, {"seconds": 30}, "mic", "sample-new", revision)
        self.assertEqual(original, self.registry.catalog())

    def test_different_people_can_save_from_same_catalog_but_same_person_cannot_overwrite(self):
        rows = self.registry.catalog()['people']
        revision = self.registry.revision()
        self.registry.save('甲', [1, 0], {'seconds': 30}, 'mic', 'sample-a', revision, rows[0]['profile_revision'])
        self.registry.save('乙', [0, 1], {'seconds': 30}, 'mic', 'sample-b', revision, rows[1]['profile_revision'])
        with self.assertRaises(ValueError):
            self.registry.save('甲', [0, 1], {'seconds': 30}, 'mic', 'sample-stale', revision, rows[0]['profile_revision'])

    def test_failed_disk_write_leaves_active_profile_untouched(self):
        original = self.registry.catalog()
        with patch('voiceprint_registry.os.replace', side_effect=OSError("full")):
            with self.assertRaises(OSError):
                self.registry.save("甲", [0, 1], {"seconds": 30}, "mic", "sample-new", original['revision'])
        self.assertEqual(original, self.registry.catalog())

    def test_delete_all_does_not_restore_seed_after_restart(self):
        self.registry.delete("甲", self.registry.revision())
        self.registry.delete("乙", self.registry.revision())
        reloaded = VoiceprintRegistry(self.path, self.seed, ["甲", "乙"])
        self.assertTrue(all(not p['registered'] for p in reloaded.catalog()['people']))
        reloaded.save("甲", [1, 0], {"seconds": 30}, "mic", "sample-again", reloaded.revision())

    def test_quality_rejects_silence_and_reports_clipping_without_claiming_identity(self):
        quiet = audio_quality(bytes(16000 * 2))
        self.assertEqual(0, quiet['active_seconds'])
        loud = audio_quality(struct.pack('<h', 32767) * 16000)
        self.assertEqual(1, loud['active_seconds'])
        self.assertEqual(1, loud['clipped_ratio'])

    def test_backend_requires_consent_and_rejects_test_wav_before_enrolling(self):
        from local_backend import Backend, ApiError
        from types import SimpleNamespace
        backend = Backend.__new__(Backend)
        called = []
        backend.speakers = SimpleNamespace(ready=True, registry=self.registry, accept=.5, margin=.05,
            enroll=lambda *args: called.append(args))
        req = {'name': '林春源', 'source': 'mic', 'sample_rate': 16000,
               'pcm_base64': __import__('base64').b64encode(bytes(15 * 32000)).decode(),
               'sample_id': 'sample-test-123', 'expected_revision': self.registry.revision()}
        for change in ({}, {'consent': True}, {'consent': True, 'single_speaker': True, 'source': 'wav'}):
            with self.assertRaises(ApiError):
                backend.voiceprint_manage(req | change, 'enroll')
        self.assertEqual([], called)

    def test_model_is_not_invoked_for_silent_or_clipped_enrollment(self):
        from local_backend import Speakers
        speaker = Speakers.__new__(Speakers)
        for pcm in (bytes(15 * 32000), struct.pack('<h', 32767) * (15 * 16000)):
            with self.assertRaises(ValueError):
                speaker.enroll({}, pcm)


if __name__ == '__main__':
    unittest.main()
