#!/usr/bin/env python3
"""Deploy the speaker-latency release (deferred trial split, split endpoint, timing logs, timing.jsonl archive).

On the Mac, inside the repo (builds the release directory; changes nothing else):
    python3 deploy/live-speed-upgrade.py build /tmp/live-speed-release [--baseline-rev 9440115]
On the HK host as root, after uploading that directory:
    python3 live-speed-upgrade.py /opt/oops/releases/live-speed             # preflight only
    python3 live-speed-upgrade.py /opt/oops/releases/live-speed --apply     # back up, replace, restart, verify, or roll back

Replaces only the listed source files. Never touches people, voiceprints, environment files, Feishu tasks or the
proxy. Also repairs the mode of models/voiceprint/split-validation.json (root:oops 0640) so the service can read it.
The phone app is backward compatible with the old server and the new server with the old app, so the order of
server and app installs does not matter for correctness; install the server first to get timing.jsonl archived.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

FILES = ('scripts/local_backend.py', 'scripts/speaker_attribution.py', 'scripts/speaker_preview.py',
         'scripts/recordings.py')
VALIDATION = 'models/voiceprint/split-validation.json'
APP = Path('/opt/oops/app')
BASE = 'http://127.0.0.1:8787'
PROTECTED = (Path('/var/lib/oops/backend/people.json'), Path('/var/lib/oops/backend/voiceprints.json'),
             Path('/var/lib/oops/backend.env'), Path('/var/lib/oops/jev.env'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build(out, rev):
    root = Path(__file__).resolve().parent.parent
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {'schema': 1, 'files': {}, 'baseline': {}}
    for name in FILES:
        target = out / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, target)
        compile(target.read_bytes(), str(target), 'exec')
        manifest['files'][name] = sha(target)
        old = subprocess.check_output(['git', '-C', str(root), 'show', f'{rev}:{name}'])
        manifest['baseline'][name] = hashlib.sha256(old).hexdigest()
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    shutil.copyfile(Path(__file__).resolve(), out / 'live-speed-upgrade.py')
    print(json.dumps({'built': str(out), 'baseline_rev': rev, 'files': manifest['files']}, indent=1))


def request(path, body=None, token='', expected=200):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            code, result = response.status, json.load(response)
    except urllib.error.HTTPError as error:
        code, result = error.code, json.load(error)
        error.close()
    if code != expected:
        raise RuntimeError(f'{path}: HTTP {code}, expected {expected}')
    return result


def service_token():
    pid = subprocess.check_output(['systemctl', 'show', 'oops-backend', '-p', 'MainPID', '--value'], text=True).strip()
    environment = dict(item.split('=', 1) for item in Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in item)
    return environment['OOPS_API_TOKEN']


def strip(health):
    return {k: v for k, v in health.items() if k != 'jev_stats'}


def wait_healthy(token):
    for _ in range(30):
        try:
            return request('/healthz', token=token)
        except (OSError, RuntimeError, ValueError):
            time.sleep(1)
    raise RuntimeError('Backend did not recover')


def smoke(token, stamp):
    """Exercises the new endpoints with silence; no meeting text, nothing persisted."""
    sid = 'speed-deploy-' + stamp
    pcm = base64.b64encode(bytes(4 * 32000)).decode()
    body = dict(session_id=sid, utterance_id='u1', sample_rate=16000, pcm_base64=pcm, text='甲乙', clip_start_ms=0, words=[])
    request('/v1/speaker/split', dict(session_id=sid, utterance_id='u1'), expected=401)
    old = request('/v1/speaker', body, token)                     # old app: synchronous
    assert 'split_pending' not in old and 'identify_ms' in old['timing_ms'], old
    started = time.monotonic()
    new = request('/v1/speaker', dict(body, utterance_id='u2', split='deferred'), token)
    assert new.get('split_pending') is True and time.monotonic() - started < 5, new
    late = request('/v1/speaker/split', dict(session_id=sid, utterance_id='u2'), token)
    assert late['status'] in ('done', 'pending', 'unavailable'), late
    assert request('/v1/speaker/split', dict(session_id=sid, utterance_id='nope'), token)['status'] == 'unavailable'
    return {'sync_ms': old['timing_ms'], 'deferred_ms': new['timing_ms'], 'split_status': late['status']}


def apply(release, manifest, token, before, stamp):
    backup = Path('/var/backups/oops-meeting') / ('speed-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(APP, backup / 'app')
    hashes = {str(p): sha(p) for p in PROTECTED if p.exists()}
    changed = False
    try:
        for name in FILES:
            changed = True
            target = APP / name
            stage = target.with_name(target.name + '.speed-stage')
            shutil.copyfile(release / name, stage)
            stage.chmod(0o644)
            os.replace(stage, target)
            assert sha(target) == manifest['files'][name]
        subprocess.run(['/opt/oops/venv/bin/python', '-m', 'py_compile', *(str(APP / n) for n in FILES)], check=True)
        validation = APP / VALIDATION
        if validation.exists():  # the service runs as `oops`; a root-only file made startup log PermissionError
            shutil.chown(validation, 'root', 'oops')
            validation.chmod(0o640)
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        after = wait_healthy(token)
        for key in before:
            if key != 'jev_stats' and after.get(key) != before[key]:  # counters restart with the process
                raise RuntimeError('Capability changed after restart: ' + key)
        if validation.exists():
            subprocess.run(['runuser', '-u', 'oops', '--', 'test', '-r', str(validation)], check=True)
        checks = smoke(token, stamp)
        for path, digest in hashes.items():
            assert sha(path) == digest, 'Protected configuration changed'
        result = dict(backup=str(backup), health=after, smoke=checks, protected_configuration_unchanged=True,
                      source_sha256=manifest['files'])
        (backup / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
    except BaseException:
        if changed:
            for name in FILES:
                original = backup / 'app' / name
                if original.exists():
                    shutil.copy2(original, APP / name)
            subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
            assert strip(wait_healthy(token)) == strip(before)
            print('ROLLED BACK source; original environment/state retained', flush=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('target', nargs='+')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--baseline-rev', default='9440115')
    args = parser.parse_args()
    if args.target[0] == 'build':
        return build(args.target[1], args.baseline_rev)
    release = Path(args.target[0]).resolve()
    assert os.geteuid() == 0 and APP.is_dir(), 'Existing HK host/root required'
    manifest = json.loads((release / 'manifest.json').read_text())
    assert manifest.get('schema') == 1 and set(manifest['files']) == set(FILES), 'Unexpected release contents'
    for name in FILES:
        assert sha(release / name) == manifest['files'][name], 'Release checksum mismatch: ' + name
        compile((release / name).read_bytes(), str(release / name), 'exec')
    for name, digest in manifest['baseline'].items():
        assert sha(APP / name) == digest, 'Live source drifted; review before deploying: ' + name
    assert subprocess.check_output(['systemctl', 'is-active', 'oops-backend'], text=True).strip() == 'active'
    token = service_token()
    before = request('/healthz', token=token)
    if not args.apply:
        print(json.dumps(dict(preflight='passed', apply=False, health=before)))
        return
    apply(release, manifest, token, before, dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))


if __name__ == '__main__':
    main()
