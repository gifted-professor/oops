#!/usr/bin/env python3
"""Guarded HK source deployment. Run on the existing host as root.

Checks verified release/baseline/model hashes, backs up, replaces source and
disabled validation only, checks auth/draft/text sync, rolls back on failure.
Never writes a Feishu task, records audio, or replaces registered voiceprints.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.error
import urllib.request

APP = Path('/opt/oops/app')
BASE = 'http://127.0.0.1:8787'
SOURCES = ('scripts/local_backend.py', 'scripts/voiceprint_registry.py',
           'scripts/speaker_attribution.py', 'scripts/speaker_validation.py',
           'scripts/voiceprint_split.py', 'scripts/speaker_preview.py',
           'scripts/meeting_features.py', 'scripts/voiceprint.py')
SOURCES += ('scripts/speaker_acceptance.py',)
VALIDATION = 'models/voiceprint/split-validation.json'
FILES = SOURCES + (VALIDATION,)
ALLOWED_SIGNERS = Path('/etc/oops/acceptance_allowed_signers')
ACCEPTANCE_FILES = {'report.json', 'report.json.sig', 'frozen.json', 'calibrators.json'}
PROTECTED = (Path('/var/lib/oops/backend/people.json'), Path('/var/lib/oops/backend/voiceprints.json'),
             Path('/var/lib/oops/backend.env'), Path('/var/lib/oops/jev.env'), ALLOWED_SIGNERS)


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def service_token():
    subprocess.run(['systemctl', 'is-active', 'oops-backend'], check=True, stdout=subprocess.DEVNULL)
    pid = subprocess.check_output(['systemctl', 'show', 'oops-backend', '-p', 'MainPID', '--value'], text=True).strip()
    environment = dict(item.split('=', 1) for item in Path('/proc/' + pid + '/environ').read_text().split('\0') if '=' in item)
    token = environment.get('OOPS_API_TOKEN')
    if not token: raise RuntimeError('An authenticated running backend is required')
    return token


def healthy(token):
    for _ in range(20):
        try: return request('/healthz', token)
        except (OSError, RuntimeError): time.sleep(1)
    raise RuntimeError('Backend did not recover')


def protected_hashes():
    return {str(path): sha(path) if path.is_file() else None for path in PROTECTED}


def assert_protected(hashes):
    if protected_hashes() != hashes: raise RuntimeError('Protected configuration or registered voiceprints changed')


def trust_signer(public_key, apply=False):
    """One-time administrator provisioning, separate from every acceptance package."""
    parts = Path(public_key).read_text().strip().split()
    if len(parts) < 2 or parts[0] != 'ssh-ed25519': raise RuntimeError('An Ed25519 SSH public key is required')
    subprocess.run(['ssh-keygen', '-l', '-f', str(public_key)], check=True, stdout=subprocess.DEVNULL, timeout=10)
    line = 'oops-acceptance namespaces="oops-speaker-acceptance" ' + ' '.join(parts[:2]) + '\n'
    if ALLOWED_SIGNERS.exists():
        if ALLOWED_SIGNERS.is_symlink() or ALLOWED_SIGNERS.read_text() != line:
            raise RuntimeError('A different signer is already pinned; bundles cannot rotate trust')
        print(json.dumps(dict(trusted_signer='already_pinned', path=str(ALLOWED_SIGNERS), apply=apply)))
        return
    if apply:
        ALLOWED_SIGNERS.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        fd = os.open(ALLOWED_SIGNERS, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, 'w') as target:
            target.write(line); target.flush(); os.fsync(target.fileno())
    print(json.dumps(dict(trusted_signer='pinned' if apply else 'preflight_passed', path=str(ALLOWED_SIGNERS),
                          apply=apply, backend_restarted=False, switches_enabled=False)))


def verify_acceptance(package):
    """Run the deployed verifier with the deployed model and the live registry."""
    package = Path(package).resolve()
    if {path.name for path in package.iterdir()} != ACCEPTANCE_FILES:
        raise RuntimeError('Acceptance package must contain only report/signature/frozen/calibrators')
    if any(path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000 for path in package.iterdir()):
        raise RuntimeError('Invalid acceptance package file')
    program = '''import json, sys
from pathlib import Path
sys.path.insert(0, "/opt/oops/app/scripts")
from local_backend import Speakers
from speaker_validation import verify_validation
speaker = Speakers(.5, .05, registry_path="/var/lib/oops/backend/voiceprints.json", require_registry=True)
if not speaker.ready: raise RuntimeError("Live speaker model did not load")
path = Path(sys.argv[1]) / "report.json"
report = json.loads(path.read_text())
if report.get("kind") != "signed-deployment-receipt": raise RuntimeError("A signed compact receipt is required")
result = verify_validation(report, path, speaker, include_disabled=True)
if result is None: raise RuntimeError("Receipt signature, active fingerprint or independent gates failed verification")
print(json.dumps({k: result[k] for k in ("split_display_enabled", "strict_a_enabled", "self_owner_enabled", "named_owner_enabled")}))
'''
    output = subprocess.check_output(['/opt/oops/venv/bin/python', '-c', program, str(package)],
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), timeout=90, stderr=subprocess.PIPE, text=True)
    return json.loads(output)


def install_acceptance(package, apply=False):
    package = Path(package).resolve()
    before_hashes = protected_hashes()
    verified = verify_acceptance(package)
    token, stamp = service_token(), dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    before = request('/healthz', token)
    assert_protected(before_hashes)
    if not apply:
        print(json.dumps(dict(preflight='passed', apply=False, verified_switches=verified,
                              audio_truth_or_vectors_uploaded=False)))
        return
    backup = Path('/var/backups/oops-meeting') / ('acceptance-' + stamp)
    backup.mkdir(mode=0o700)
    target = APP / VALIDATION
    old_link = os.readlink(target) if target.is_symlink() else None
    old_file = target.is_file() and old_link is None
    if old_file: shutil.copy2(target, backup / 'split-validation.json')
    (backup / 'prior-target.json').write_text(json.dumps({'link': old_link, 'file': old_file, 'health': before,
                                                       'protected_hashes': before_hashes}) + '\n')
    generation = target.parent / 'acceptance' / (stamp + '-' + sha(package / 'report.json')[:12])
    generation.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    generation.mkdir(mode=0o755)
    for name in ACCEPTANCE_FILES:
        shutil.copyfile(package / name, generation / name)
        (generation / name).chmod(0o644)
    sid, changed = 'acceptance-smoke-' + stamp, False
    stage = target.with_name(target.name + '.acceptance-stage')
    try:
        if verify_acceptance(generation) != verified: raise RuntimeError('Staged receipt verification changed')
        assert_protected(before_hashes)
        stage.unlink(missing_ok=True)
        os.symlink(os.path.relpath(generation / 'report.json', target.parent), stage)
        os.replace(stage, target); changed = True
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        after = healthy(token)
        expected = dict(segments_validated=verified['split_display_enabled'],
                        strict_a_enabled=verified['strict_a_enabled'], self_owner_enabled=verified['self_owner_enabled'],
                        named_owner_enabled=verified['named_owner_enabled'])
        if any(after.get(key) is not value for key, value in expected.items()):
            raise RuntimeError('Loaded attribution switches disagree with verified receipt')
        for key in ('voiceprint', 'jev', 'feishu', 'meetings', 'notices', 'speaker_preview'):
            if before.get(key) != after.get(key): raise RuntimeError('Existing capability changed: ' + key)
        smoke(token, sid)
        assert_protected(before_hashes)
        result = dict(backup=str(backup), receipt=str(generation / 'report.json'), health=after,
                      checks='pinned signature, active fingerprint, independent gates, auth, segment sync, no-prefill drafts',
                      audio_truth_or_vectors_uploaded=False, feishu_writes=0)
        (backup / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False))
    except BaseException:
        try: request('/v1/meetings/delete', token, dict(session_id=sid, confirm=True))
        except Exception: pass
        stage.unlink(missing_ok=True)
        if changed:
            if old_link is not None:
                os.symlink(old_link, stage); os.replace(stage, target)
            elif old_file:
                shutil.copy2(backup / 'split-validation.json', stage); os.replace(stage, target)
            else:
                target.unlink(missing_ok=True)
            subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
            if healthy(token) != before: raise RuntimeError('Acceptance rollback health mismatch')
        print('ROLLED BACK: previous acceptance target; voiceprints and trust retained')
        raise


def request(path, token='', body=None, expected=200):
    headers = {'Content-Type': 'application/json'}
    if token: headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path, headers=headers,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            code, result = response.status, json.load(response)
    except urllib.error.HTTPError as error:
        code, result = error.code, json.load(error)
        error.close()
    if code != expected: raise RuntimeError(f'{path}: HTTP {code}, expected {expected}')
    return result


def smoke(token, sid):
    request('/healthz', expected=401)
    for route in ('/v1/actions/draft', '/v1/meetings/sync'):
        request(route, body={}, expected=401)
    original = '我来做。'
    segment = dict(start_ms=0, end_ms=1000, text=original, speaker=None,
                   score=0, confidence=None, tier='C', overlap=True, approx=False,
                   source=None, candidates=[dict(name='林春源', score=.4)])
    row = dict(id='u1', text=original, start_ms=0, end_ms=1000,
               speaker=None, tag='commitment', segments=[segment])
    body = dict(session_id=sid, started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                time_zone='UTC', utterances=[row], notices=[])
    request('/v1/meetings/sync', token, body)
    saved = request('/v1/meetings/' + sid, token)
    assert saved['utterances'][0]['segments'] == [segment], 'Segment sync did not round-trip'
    for text in ('我来做。', '我们下周完成。', '春源，你周五前把方案发我。'):
        current = dict(row, text=text, segments=[])
        draft = request('/v1/actions/draft', token, dict(session_id=sid, utterances=[current],
            context=[], reference_time=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone='Asia/Shanghai'))
        assert draft.get('owner') is None and draft.get('who') is None, 'Unvalidated owner was prefilled'
        assert draft.get('tier') != 'A', 'A gate unexpectedly enabled'
        if text.startswith('我们'): assert draft.get('suggested_owner') is None
    overlapping = request('/v1/actions/draft', token, dict(session_id=sid, utterances=[row], context=[]))
    assert overlapping.get('owner') is None and overlapping.get('suggested_owner') is None
    assert overlapping.get('tier') == 'C', 'Overlap unexpectedly received owner attribution'
    request('/v1/meetings/delete', token, dict(session_id=sid, confirm=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('release', type=Path, nargs='?')
    parser.add_argument('--trust-signer', type=Path, metavar='PUBLIC_KEY')
    parser.add_argument('--install-acceptance', type=Path, metavar='PACKAGE')
    parser.add_argument('--apply', action='store_true', help='Apply only after preflight; default is read-only preflight')
    args = parser.parse_args()
    if os.geteuid() != 0 or not APP.is_dir(): raise RuntimeError('Existing HK host/root required')
    if sum(value is not None for value in (args.release, args.trust_signer, args.install_acceptance)) != 1:
        parser.error('Choose exactly one release, --trust-signer, or --install-acceptance')
    if args.trust_signer: return trust_signer(args.trust_signer, args.apply)
    if args.install_acceptance: return install_acceptance(args.install_acceptance, args.apply)
    release = args.release.resolve()
    manifest = json.loads((release / 'manifest.json').read_text())
    if manifest.get('schema') != 1 or set(manifest['files']) != set(FILES):
        raise RuntimeError('Unexpected release contents')
    for name in FILES:
        if sha(release / name) != manifest['files'][name]: raise RuntimeError('Release checksum mismatch: ' + name)
    for name in SOURCES: compile((release / name).read_bytes(), str(release / name), 'exec')
    for name, digest in manifest['baseline'].items():
        if name not in SOURCES or sha(APP / name) != digest: raise RuntimeError('Live source drifted: ' + name)
    for name, digest in manifest['models'].items():
        if name not in ('models/campplus_zh.onnx', 'models/sherpa-onnx-pyannote-segmentation-3-0/model.onnx'):
            raise RuntimeError('Unexpected model')
        if sha(APP / name) != digest: raise RuntimeError('Model drifted: ' + name)
    disabled = json.loads((release / VALIDATION).read_text())
    if (disabled.get('schema') != 2 or disabled.get('finalized') is not False
            or set(disabled.get('switches', {})) != {'display', 'strict_a', 'self_owner', 'named_owner'}
            or any(value is not False for value in disabled['switches'].values())
            or any(disabled.get(k, False) is not False for k in ('split_passed', 'strict_a_enabled', 'self_owner_enabled', 'named_owner_enabled'))
            or any(disabled.get('config', {}).get(k, False) is not False for k in ('strict_a_enabled', 'self_owner_enabled', 'named_owner_enabled'))):
        raise RuntimeError('First deployment must keep every attribution gate disabled')
    token = service_token()
    before = request('/healthz', token)
    if not args.apply:
        print(json.dumps(dict(preflight='passed', apply=False, source_files=len(SOURCES), gates='disabled')))
        return
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/oops-meeting') / ('attribution-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(APP, backup / 'app')
    protected = PROTECTED
    hashes = protected_hashes()
    for path in protected:
        if path.is_file():
            saved = backup / ('protected-' + path.parent.name + '-' + path.name)
            shutil.copy2(path, saved); saved.chmod(0o600)
    (backup / 'protected-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')
    sid, changed = 'attribution-deploy-' + stamp, []
    try:
        for name in FILES:
            target = APP / name
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = target.with_name(target.name + '.attribution-stage')
            shutil.copyfile(release / name, stage)
            # The service runs as `oops`; a root-only 0600 file made the backend log PermissionError on startup.
            stage.chmod(0o640 if name == VALIDATION else 0o644)
            if name == VALIDATION: shutil.chown(stage, 'root', 'oops')
            os.replace(stage, target); changed.append(name)
        subprocess.run(['/opt/oops/venv/bin/python', '-m', 'py_compile', *(str(APP / n) for n in SOURCES)], check=True)
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        after = None
        for _ in range(20):
            try:
                after = request('/healthz', token); break
            except (OSError, RuntimeError): time.sleep(1)
        if not after: raise RuntimeError('Backend did not recover')
        subprocess.run(['runuser', '-u', 'oops', '--', 'test', '-r', str(APP / VALIDATION)], check=True)
        for key in ('segments_validated', 'strict_a_enabled', 'self_owner_enabled', 'named_owner_enabled'):
            if after.get(key) is not False: raise RuntimeError('Unexpected attribution gate: ' + key)
        for key in ('voiceprint', 'jev', 'feishu', 'meetings', 'notices', 'speaker_preview'):
            if after.get(key) != before.get(key): raise RuntimeError('Existing capability changed: ' + key)
        smoke(token, sid)
        assert_protected(hashes)
        summary = dict(backup=str(backup), health=after, checks='auth, segment roundtrip, no-prefill drafts',
                       feishu_writes=0, protected_configuration_unchanged=True,
                       source_sha256=manifest['files'])
        (backup / 'verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(summary, ensure_ascii=False))
    except BaseException:
        try: request('/v1/meetings/delete', token, dict(session_id=sid, confirm=True))
        except Exception: pass
        for name in reversed(changed):
            original, target = backup / 'app' / name, APP / name
            if original.is_file(): shutil.copy2(original, target)
            else: target.unlink(missing_ok=True)
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        if request('/healthz', token) != before: raise RuntimeError('Rollback health mismatch')
        print('ROLLED BACK: source and gates; protected state retained')
        raise


if __name__ == '__main__': main()
