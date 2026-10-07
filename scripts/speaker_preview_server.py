#!/usr/bin/env python3
"""Loopback-only preview lab; enrolled identities stay on the real HTTPS backend.

No enrollment, deletion, Feishu or business proxy endpoints. Intended for USB device
validation before deploying the same segmentation model to the production server.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import threading
import types
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from local_backend import Backend, ApiError, load_jev
from speaker_preview import SpeakerPreview


class RemoteVoiceprints:
    def __init__(self, base, token):
        if not base.startswith('https://'):
            raise ValueError('Registered voiceprint API requires HTTPS')
        self.base, self.token = base.rstrip('/'), token
        self.cache, self.lock = {}, threading.Lock()

    def diagnose(self, pcm):
        key = hashlib.sha256(pcm).hexdigest()
        with self.lock:
            if key in self.cache:
                return self.cache[key]
        body = json.dumps({'sample_rate': 16000, 'pcm_base64': base64.b64encode(pcm).decode()}).encode()
        request = urllib.request.Request(self.base + '/v1/voiceprints/test', data=body,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=4) as response:
            result = json.load(response)
        with self.lock:
            if len(self.cache) >= 64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = result
        return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8790)
    p.add_argument('--backend', default='https://giftedprofessor.xyz/oops-meeting')
    p.add_argument('--env-file', default='.local-backend/backend.env')
    p.add_argument('--model', default='models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx')
    p.add_argument('--jev-env', default='.jev.env')
    p.add_argument('--jev-model', default='jev-1.13.0')
    p.add_argument('--no-jev', action='store_true')
    args = p.parse_args()
    values = dict(line.split('=', 1) for line in Path(args.env_file).read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    token = values.get('OOPS_API_TOKEN', '').strip().strip('"\x27')
    if not token:
        raise ValueError('Missing private API token')
    backend = Backend.__new__(Backend)
    backend.jev = load_jev(args)
    backend.speaker_preview = SpeakerPreview(args.model, RemoteVoiceprints(args.backend, token))
    admission = threading.BoundedSemaphore(3)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def reply(self, code, data):
            raw = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers(); self.wfile.write(raw)

        def do_POST(self):
            self.connection.settimeout(12)
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), ('Bearer ' + token).encode()):
                return self.reply(401, {'error': {'message': 'Unauthorized'}})
            if self.path != '/v1/speaker-preview':
                return self.reply(404, {'error': {'message': 'Preview only'}})
            if not admission.acquire(blocking=False):
                return self.reply(429, {'error': {'message': 'Preview busy'}})
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 2_000_000:
                    raise ValueError('Invalid body size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('Invalid body')
                result = backend.preview_speakers(body)
                self.reply(200, result)
            except ApiError as error:
                self.reply(error.status, {'error': {'message': error.message}})
            except (ValueError, TypeError):
                self.reply(400, {'error': {'message': 'Invalid preview request'}})
            except Exception:
                self.reply(503, {'error': {'message': 'Preview temporarily unavailable'}})
            finally:
                admission.release()

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Experimental speaker preview listening on loopback:{args.port}; read-only remote voiceprints', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__': main()
