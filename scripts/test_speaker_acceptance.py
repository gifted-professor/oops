"""Real SSH signature round trips on temporary keys; never installs an acceptance."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from speaker_acceptance import (SIGNER_ID, SIGNATURE_NAMESPACE, init_signing_key,
                                verify_receipt_signature)


@unittest.skipUnless(shutil.which('ssh-keygen'), 'OpenSSH ssh-keygen is required')
class ReceiptSignatureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.first, self.second = self.base / 'first', self.base / 'second'
        for key in (self.first, self.second):
            subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(key)],
                           check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.allowed = self.base / 'allowed_signers'
        self.pin(self.first)
        self.report = self.base / 'report.json'
        # This deliberately is not a passing acceptance report.
        self.report.write_text(json.dumps({'test_only': True, 'switches': {'display': False}}) + '\n')
        subprocess.run(['ssh-keygen', '-Y', 'sign', '-f', str(self.first), '-n', SIGNATURE_NAMESPACE, str(self.report)],
                       check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def pin(self, key):
        public = Path(str(key) + '.pub').read_text().split()
        self.allowed.write_text(SIGNER_ID + ' namespaces="' + SIGNATURE_NAMESPACE + '" ' + ' '.join(public[:2]) + '\n')
        self.allowed.chmod(0o644)

    def test_real_signature_roundtrip_and_resolved_symlink(self):
        self.assertTrue(verify_receipt_signature(self.report, self.allowed))
        link = self.base / 'split-validation.json'
        link.symlink_to(self.report.name)
        self.assertTrue(verify_receipt_signature(link, self.allowed))

    def test_tampered_receipt_is_rejected(self):
        self.report.write_text('{"test_only":true,"switches":{"display":true}}\n')
        self.assertFalse(verify_receipt_signature(self.report, self.allowed))

    def test_missing_or_different_trusted_signer_is_rejected(self):
        self.assertFalse(verify_receipt_signature(self.report, self.base / 'not-pinned'))
        self.pin(self.second)
        self.assertFalse(verify_receipt_signature(self.report, self.allowed))

    def test_missing_signature_and_writable_trust_are_rejected(self):
        self.allowed.chmod(0o666)
        self.assertFalse(verify_receipt_signature(self.report, self.allowed))
        self.allowed.chmod(0o644)
        Path(str(self.report) + '.sig').unlink()
        self.assertFalse(verify_receipt_signature(self.report, self.allowed))

    def test_key_initialization_is_idempotent_and_private(self):
        key = self.base / 'new-key'
        init_signing_key(key)
        original = key.read_bytes()
        init_signing_key(key)
        self.assertEqual(original, key.read_bytes())
        self.assertEqual(0, key.stat().st_mode & 0o077)

    def test_remote_trust_is_preflight_only_and_cannot_rotate(self):
        script = Path(__file__).resolve().parent.parent / 'deploy/speaker-attribution-upgrade.py'
        spec = importlib.util.spec_from_file_location('acceptance_upgrade_test', script)
        upgrade = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upgrade)
        pinned = self.base / 'server-config/allowed_signers'
        with patch.object(upgrade, 'ALLOWED_SIGNERS', pinned):
            upgrade.trust_signer(Path(str(self.first) + '.pub'), apply=False)
            self.assertFalse(pinned.exists())
            upgrade.trust_signer(Path(str(self.first) + '.pub'), apply=True)
            original = pinned.read_bytes()
            upgrade.trust_signer(Path(str(self.first) + '.pub'), apply=True)
            self.assertEqual(original, pinned.read_bytes())
            with self.assertRaises(RuntimeError):
                upgrade.trust_signer(Path(str(self.second) + '.pub'), apply=True)
            self.assertEqual(original, pinned.read_bytes())


if __name__ == '__main__':
    unittest.main()
