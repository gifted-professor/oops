#!/usr/bin/env python3
"""Exercise server authentication before any Feishu side effect."""
import json
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from local_backend import Handler
from voiceprint_invites import issue_invite, invite_person


class AuthenticationTest(unittest.TestCase):
    def test_invitation_signature_expiry_and_person_scope(self):
        import time
        from local_backend import TEAM
        token = issue_invite('private-enroll-key', TEAM[0], time.time() + 60)
        self.assertEqual(TEAM[0], invite_person('private-enroll-key', token, TEAM))
        for bad in (token + '0', 'vp1.bad.bad', '', 'not-an-invite', token.replace('vp1.', 'vp2.')):
            self.assertIsNone(invite_person('private-enroll-key', bad, TEAM))
        self.assertIsNone(invite_person('private-enroll-key', issue_invite('private-enroll-key', TEAM[0], 1), TEAM))
        calls = []
        backend = types.SimpleNamespace(voiceprint_catalog=lambda: {'people': [{'name': n} for n in TEAM], 'revision': 'r'},
            voiceprint_manage=lambda body, action: calls.append((body, action)) or {'people': [{'name': n} for n in TEAM], 'revision': 'r'})
        class Scoped(Handler):
            api_token = 'test-full-key'
            enroll_secret = 'private-enroll-key'
        Scoped.backend = backend
        server = ThreadingHTTPServer(('127.0.0.1', 0), Scoped)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(path, body=None, expected=200):
            req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}' + path,
                data=json.dumps(body).encode() if body is not None else None,
                headers={'Authorization': 'Bearer ' + token})
            if expected != 200:
                with self.assertRaises(urllib.error.HTTPError) as result:
                    urllib.request.urlopen(req)
                self.assertEqual(expected, result.exception.code)
                result.exception.close()
            else:
                with urllib.request.urlopen(req) as response:
                    return json.load(response)
        try:
            catalog = request('/v1/voiceprints')
            self.assertEqual([{'name': TEAM[0]}], catalog['people'])
            self.assertEqual(TEAM[0], catalog['person'])
            for path, body in (('/healthz', None), ('/v1/voiceprints/delete', {}), ('/v1/actions/confirm', {}),
                               ('/v1/speaker-preview', {}),
                               ('/v1/voiceprints/enroll', {'name': TEAM[1]})):
                request(path, body, 403)
            request('/v1/voiceprints/enroll', [], 400)
            self.assertEqual([], calls)
            self.assertEqual(TEAM[0], request('/v1/voiceprints/enroll', {'name': TEAM[0]})['person'])
            request('/v1/voiceprints/test', {'sample_rate': 16000})
            self.assertEqual(2, len(calls))
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/voiceprint/') as response:
                self.assertIn('frame-ancestors', response.headers['Content-Security-Policy'])
                self.assertEqual('no-store', response.headers['Cache-Control'])
                self.assertNotIn(token.encode(), response.read())
        finally:
            server.shutdown(); server.server_close(); worker.join()

    def test_every_endpoint_requires_token_before_dispatch(self):
        calls = []
        backend = types.SimpleNamespace(jev=None, speakers=types.SimpleNamespace(ready=True),
            args=types.SimpleNamespace(feishu="dry-run"), split_config=None,
            confirm=lambda body: calls.append(body) or {"op_id": "op-test-123", "status": "pending"})
        class Protected(Handler):
            api_token = "test-key"
        Protected.backend = backend
        server = ThreadingHTTPServer(("127.0.0.1", 0), Protected)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            for path, body in (("/healthz", None), ("/v1/actions/op-test-123", None),
                               ("/v1/actions/confirm", {}), ("/v1/utterances", {}), ("/v1/speaker", {}),
                               ("/v1/voiceprints", None), ("/v1/voiceprints/enroll", {}),
                               ("/v1/voiceprints/delete", {}), ("/v1/voiceprints/test", {}), ("/v1/speaker-preview", {})):
                for token in (None, "wrong"):
                    headers = {"Authorization": "Bearer " + token} if token else {}
                    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                                 headers=headers)
                    with self.assertRaises(urllib.error.HTTPError) as result:
                        urllib.request.urlopen(req)
                    self.assertEqual(401, result.exception.code)
                    result.exception.close()
            self.assertEqual([], calls)
            req = urllib.request.Request(base + "/healthz", headers={"Authorization": "Bearer test-key"})
            with urllib.request.urlopen(req) as response:
                self.assertEqual("ok", json.load(response)["status"])
            req = urllib.request.Request(base + "/v1/actions/confirm", data=b"{}",
                                         headers={"Authorization": "Bearer test-key"})
            with urllib.request.urlopen(req) as response:
                self.assertEqual("pending", json.load(response)["status"])
            self.assertEqual([{}], calls)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
