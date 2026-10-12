#!/usr/bin/env python3
"""Deploy the review release (per-sentence reviews, review queue, recording clips, Feishu task list).

On the Mac, inside the repo (builds the release directory; changes nothing else):
    python3 deploy/review-queue-upgrade.py build /tmp/review-release [--baseline-rev <commit now live>]
    (0e3e865 for the first review release; 5e17017 for the meeting-title update on top of it;
     3e11db9 for the meeting-end and Oops bot fixes on top of the bot release)
On the HK host as root, after uploading that directory:
    python3 review-queue-upgrade.py /opt/oops/releases/review             # preflight only
    python3 review-queue-upgrade.py /opt/oops/releases/review --apply     # back up, replace, restart, verify, or roll back

Replaces only the listed source files. Never touches people, voiceprints, environment files, meetings, recordings,
Feishu tasks or the proxy. The smoke test only reads (meeting list, one meeting, the Feishu task list) and sends
requests that must fail without writing (a review and a clip for a meeting that does not exist). The old app keeps
working with the new server, so install the server first, then the app outside a meeting.
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

FILES = ('scripts/local_backend.py', 'scripts/meeting_features.py', 'scripts/recordings.py', 'scripts/feishu_actions.py')
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
    shutil.copyfile(Path(__file__).resolve(), out / 'review-queue-upgrade.py')
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


VOLATILE = ('jev_stats', 'mac_agent_online')  # counters restart; the Mac helper is back online on its next poll


def strip(health):
    return {k: v for k, v in health.items() if k not in VOLATILE}


def wait_healthy(token):
    for _ in range(30):
        try:
            return request('/healthz', token=token)
        except (OSError, RuntimeError, ValueError):
            time.sleep(1)
    raise RuntimeError('Backend did not recover')


def smoke(token, stamp):
    """Reads, plus calls that must be refused; nothing is written to meetings, recordings or Feishu."""
    missing = 'review-deploy-' + stamp
    request('/v1/meetings/review', dict(session_id=missing, utterance_id='u1', speaker='other'), expected=401)
    request('/v1/meetings/review', dict(session_id=missing, utterance_id='u1', speaker='other'), token, expected=404)
    request(f'/v1/recordings/{missing}/clip?start_ms=0&end_ms=500', token=token, expected=404)
    listing = request('/v1/meetings?limit=5', token=token)
    assert all('review_pending' in item and 'title_source' in item for item in listing['items']), listing
    queue = None
    if listing['items']:
        opened = request('/v1/meetings/' + listing['items'][0]['session_id'], token=token)
        assert isinstance(opened.get('review_queue'), list), 'review_queue missing'
        queue = len(opened['review_queue'])
    tasks = request('/v1/tasks?status=all', token=token)['items']
    return {'meetings': listing['count'], 'latest_queue': queue,
            'tasks': [(t['op_id'][:8], t['feishu']['state']) for t in tasks]}


def apply(release, manifest, token, before, stamp):
    backup = Path('/var/backups/oops-meeting') / ('review-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(APP, backup / 'app')
    hashes = {str(p): sha(p) for p in PROTECTED if p.exists()}
    changed = False
    try:
        for name in FILES:
            changed = True
            target = APP / name
            stage = target.with_name(target.name + '.review-stage')
            shutil.copyfile(release / name, stage)
            stage.chmod(0o644)
            os.replace(stage, target)
            assert sha(target) == manifest['files'][name]
        subprocess.run(['/opt/oops/venv/bin/python', '-m', 'py_compile', *(str(APP / n) for n in FILES)], check=True)
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        after = wait_healthy(token)
        for key in before:
            if key not in VOLATILE and after.get(key) != before[key]:
                raise RuntimeError('Capability changed after restart: ' + key)
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
    parser.add_argument('--baseline-rev', default='0e3e865')
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
