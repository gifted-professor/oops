#!/usr/bin/env python3
"""Run as root on the existing HK host after uploading a verified P0 release directory.
Back up source/config, replace only two Python modules, verify, or restore on failure.
Never prints credentials or alters people, voiceprints, Feishu tasks or proxy settings.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASELINE = '5b7941a5a61d52497b653bdda188cf8d179136259317cc9531f4724907196734'
FILES = ('scripts/local_backend.py', 'scripts/meeting_features.py')
APP = Path('/opt/oops/app')
BASE = 'http://127.0.0.1:8787'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(path, body=None, token='', expected=200):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(BASE + path, headers=headers,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            code, result = response.status, json.load(response)
    except urllib.error.HTTPError as error:
        code = error.code
        result = json.load(error)
        error.close()
    if code != expected:
        raise RuntimeError(f'{path}: HTTP {code}, expected {expected}')
    return result


def verify_routes(token, secret, sid):
    sys.path.insert(0, str(APP / 'scripts'))
    from voiceprint_invites import issue_invite
    invitation = issue_invite(secret, '林春源', time.time() + 60)
    routes = [('/v1/meetings', None), ('/v1/meetings/' + sid, None),
              ('/v1/meetings/sync', {}), ('/v1/meetings/search', {}),
              ('/v1/meetings/delete', {}), ('/v1/notices', {})]
    for path, body in routes:
        request(path, body, expected=401)
        request(path, body, invitation, 403)
    u = dict(id='u1', text='预算不能超过10万。', start_ms=0, end_ms=1000, tag='constraint')
    agenda = dict(goal='[Oops P0合成验收] 上线预算', items=[dict(id='a1', title='预算')], planned_ms=1800000)
    body = dict(session_id=sid, started_at=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone='UTC',
                agenda=agenda, utterances=[u], notices=[])
    for _ in range(2):
        assert request('/v1/meetings/sync', body, token)['utterance_count'] == 1
    assert any(m['session_id'] == sid for m in request('/v1/meetings', token=token)['items'])
    saved = request('/v1/meetings/' + sid, token=token)
    assert saved['digest']['constraint'][0]['text'] == u['text']
    assert request('/v1/meetings/search', dict(query='预算上限是多少'), token)['items']
    notice = request('/v1/notices', dict(kind='topic', session_id=sid, agenda=agenda, utterances=[u]), token)
    assert notice['topic'] == 'a1'
    assert request('/v1/notices', dict(kind='resolution', session_id=sid, item=agenda['items'][0], utterances=[u]), token)['resolved'] in (True, False, None)
    proposal = dict(u, id='u3', text='上线方案预算要13万。', tag='decision', start_ms=2000, end_ms=3000)
    assert request('/v1/notices', dict(kind='conflict', session_id=sid + '-other', utterance=proposal, previous=[]), token)['conflict']
    assert request('/v1/recall', dict(session_id=sid, trigger='ask', query='预算上限是多少', now_ms=5000, candidates=[u]), token)['picks']
    tagged = request('/v1/utterances', dict(session_id=sid, utterance_id=u['id'],
        text=u['text'], start_ms=0, end_ms=1000, context=[]), token)
    assert tagged['tag'] == 'constraint'
    assigned = dict(u, id='u2', text='春源，你周五之前把方案发我。', tag='commitment')
    draft = request('/v1/actions/draft', dict(session_id=sid, utterances=[assigned], context=[],
        reference_time=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone='Asia/Shanghai'), token)
    assert draft['who'] == '林春源' and len(draft['assignee_candidates']) == 4
    body['ended_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    request('/v1/meetings/sync', body, token)
    assert request('/v1/meetings/delete', dict(session_id=sid, confirm=True), token)['deleted']
    request('/v1/meetings/' + sid, token=token, expected=404)
    request('/v1/meetings/sync', body, token, 400)


def main():
    release = Path(sys.argv[1]).resolve()
    assert os.geteuid() == 0 and APP.is_dir(), 'Existing HK host/root required'
    manifest = json.loads((release / 'manifest.json').read_text())
    for name in FILES:
        assert sha(release / name) == manifest[name], 'Release checksum mismatch'
        py_compile.compile(str(release / name), doraise=True)
    assert sha(APP / FILES[0]) == BASELINE, 'Live source drifted; review before deploying'
    assert subprocess.check_output(['systemctl', 'is-active', 'oops-backend'], text=True).strip() == 'active'
    pid = subprocess.check_output(['systemctl', 'show', 'oops-backend', '-p', 'MainPID', '--value'], text=True).strip()
    environment = dict(item.split('=', 1) for item in Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in item)
    token = environment['OOPS_API_TOKEN']
    secret = environment['OOPS_ENROLL_SECRET']
    assert token and secret
    before = request('/healthz', token=token)
    assert before['feishu'] == 'live' and before['jev'] and before['speaker_preview']
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = Path('/var/backups/oops-meeting') / ('p0-' + stamp)
    backup.mkdir(mode=0o700)
    shutil.copytree(APP, backup / 'app')
    for name in ('backend.env', 'jev.env'):
        shutil.copy2(Path('/var/lib/oops') / name, backup / name)
        (backup / name).chmod(0o600)
    unit = subprocess.check_output(['systemctl', 'cat', 'oops-backend'], text=True)
    (backup / 'service.txt').write_text(unit)
    (backup / 'service.txt').chmod(0o600)
    protected = [Path('/var/lib/oops/backend/people.json'), Path('/var/lib/oops/backend/voiceprints.json'),
                 Path('/var/lib/oops/backend.env'), Path('/var/lib/oops/jev.env')]
    hashes = {str(p): sha(p) for p in protected if p.exists()}
    (backup / 'protected-hashes.json').write_text(json.dumps(hashes, indent=2) + '\n')
    print('backup:', backup, flush=True)
    sid = 'p0-deploy-' + stamp
    changed = False
    try:
        for name in FILES:
            changed = True
            target = APP / name
            stage = target.with_suffix('.py.p0-stage')
            shutil.copyfile(release / name, stage)
            stage.chmod(0o644)
            os.replace(stage, target)
            assert sha(target) == manifest[name]
        subprocess.run(['/opt/oops/venv/bin/python', '-m', 'py_compile', *(str(APP / n) for n in FILES)], check=True)
        subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
        after = None
        for _ in range(20):
            try:
                after = request('/healthz', token=token)
                break
            except (OSError, RuntimeError):
                time.sleep(1)
        assert after and after.get('meetings') and after.get('notices')
        assert all(after[k] == before[k] for k in ('jev', 'voiceprint', 'feishu', 'speaker_preview', 'segments_validated'))
        verify_routes(token, secret, sid)
        assert len(request('/v1/voiceprints', token=token)['people']) == 4
        for path, digest in hashes.items():
            assert sha(Path(path)) == digest, 'Protected configuration changed'
        subprocess.run(['systemctl', 'is-active', 'oops-backend'], check=True)
        result = dict(backup=str(backup), health=after, source_sha256=manifest,
                      authentication='6 routes: no-token401 / voiceprint-invite403',
                      synthetic='upsert/list/open/search/notices/recall/tag/draft/delete/tombstone passed',
                      protected_configuration_unchanged=True)
        (backup / 'verification.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except BaseException:
        # Clean up synthetic text while the upgraded endpoints are still available.
        try:
            request('/v1/meetings/delete', dict(session_id=sid, confirm=True), token)
        except Exception:
            pass
        if changed:
            for name in FILES:
                original = backup / 'app' / name
                if original.exists():
                    shutil.copy2(original, APP / name)
                else:
                    (APP / name).unlink(missing_ok=True)
            subprocess.run(['systemctl', 'restart', 'oops-backend'], check=True)
            assert request('/healthz', token=token) == before
            print('ROLLED BACK source; original environment/state retained', flush=True)
        raise
    finally:
        # Delete only this independently named synthetic meeting if checks failed midway.
        try:
            request('/v1/meetings/delete', dict(session_id=sid, confirm=True), token)
        except Exception:
            pass


if __name__ == '__main__':
    main()
