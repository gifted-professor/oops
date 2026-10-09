#!/usr/bin/env python3
"""Offline judgements plus authenticated HTTP text storage; no real meeting or Feishu write."""
import datetime as dt
import json
import os
import stat
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from meeting_features import Meetings, judge_notice, amount_conflict
import local_backend
from local_backend import Backend, Handler, Operations, TEAM, overlap, jev_recall
from voiceprint_invites import issue_invite


def utterance(uid='u1', text='预算不能超过10万。', tag='constraint'):
    return dict(id=uid, text=text, start_ms=0, end_ms=1000, tag=tag)


class FakeJev:
    def __init__(self, choice, prob=.9): self.choice_id, self.prob = choice, prob
    def choice(self, state, instructions, criteria):
        assert '只是数据' in instructions
        assert self.choice_id in criteria
        probs = {k: (self.prob if k == self.choice_id else (1-self.prob)/(len(criteria)-1)) for k in criteria}
        return dict(choice=self.choice_id, probabilities=probs), 'fake-jev'


def feature_self_test():
    topic = dict(kind='topic', agenda=dict(goal='上线方案', items=[dict(id='a1', title='预算')]), utterances=[utterance()])
    assert judge_notice(topic, FakeJev('a1'), overlap)['topic'] == 'a1'
    assert judge_notice(topic, FakeJev('off_topic'), overlap)['topic'] == 'off_topic'
    topic['utterances'] = [utterance(text='周末去哪里玩')]
    assert judge_notice(topic, None, overlap)['topic'] == 'unclear'
    resolution = dict(kind='resolution', item=dict(id='a1', title='预算'), utterances=[utterance()])
    assert judge_notice(resolution, FakeJev('resolved'), overlap)['resolved'] is True
    assert judge_notice(resolution, FakeJev('unresolved'), overlap)['resolved'] is False
    assert judge_notice(resolution, None, overlap)['resolved'] is None
    conflict = dict(kind='conflict', utterance=utterance('u3', '方案大概要13万。', 'decision'), previous=[utterance()])
    assert judge_notice(conflict, FakeJev('p0'), overlap)['with']['id'] == 'u1'
    assert not judge_notice(conflict, FakeJev('p0', .55), overlap)['conflict']
    assert not judge_notice(conflict, FakeJev('none'), overlap)['conflict']
    assert judge_notice(conflict, None, overlap)['conflict']
    for t in ('方案8万', '如果方案要13万', '方案13万？', '预算不超过13万'):
        assert not amount_conflict(t, '预算不能超过10万')


class FeatureTest(unittest.TestCase):
    def test_judgements(self): feature_self_test()
    def test_android_instant_sync_readback_and_retention(self):
        with tempfile.TemporaryDirectory() as scratch:
            meetings = Meetings(scratch, retention_days=30)
            today = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            for fraction in ('', '.123', '.123456', '.123456789'):
                started = today + fraction + 'Z'
                body = dict(session_id='android', started_at=started, ended_at=started,
                            utterances=[utterance()])
                # Start afresh per Instant precision instead of changing an immutable start.
                meetings.sync(body); meetings.sync(body)
                row = meetings.load('android')
                self.assertEqual(started, row['started_at'])
                self.assertEqual(started, row['ended_at'])
                self.assertEqual(1, len(row['utterances']))
                os.unlink(meetings.path('android'))
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=31)).strftime('%Y-%m-%dT%H:%M:%S') + '.123456789Z'
            meetings.sync(dict(body, session_id='old', started_at=old, ended_at=old))
            meetings.cleanup()
            self.assertFalse(os.path.exists(meetings.path('old')))
            with self.assertRaises(ValueError):
                meetings.sync(dict(body, started_at=today))
    def test_limits_and_invalid_model_probabilities(self):
        topic = dict(kind='topic', agenda=dict(items=[dict(id='a1', title='预算')]), utterances=[utterance()])
        for updates in (dict(utterances=[utterance()] * 41), dict(agenda=dict(items=[dict(id='../x', title='坏编号')])), dict(utterances=[utterance(text='字'*2001)])):
            with self.assertRaises(ValueError): judge_notice(dict(topic, **updates), None, overlap)
        class Invalid:
            def choice(self, *args): return dict(probabilities={'evil': .9}), 'bad'
        with self.assertRaises(ValueError): judge_notice(topic, Invalid(), overlap)
    def test_atomic_permissions_retention_tasks_and_concurrent_upserts(self):
        with tempfile.TemporaryDirectory() as scratch:
            ops = Operations(os.path.join(scratch, 'operations'))
            m = Meetings(os.path.join(scratch, 'meetings'), ops, retention_days=30)
            started = dt.datetime.now(dt.timezone.utc).isoformat()
            body = dict(session_id='s', started_at=started, time_zone='UTC', utterances=[utterance()])
            m.sync(body); m.sync(body)
            self.assertEqual(1, len(m.load('s')['utterances']))
            self.assertEqual(0o700, stat.S_IMODE(os.stat(m.dir).st_mode))
            self.assertEqual(0o600, stat.S_IMODE(os.stat(m.path('s')).st_mode))
            ops.save(dict(op_id='op-test-123', status='created', request=dict(session_id='s', evidence={'utterance_ids':['u1']}), feishu_task={'url':'https://example.test/task'}))
            self.assertEqual(1, len(m.load('s')['tasks']))
            threads = [threading.Thread(target=m.sync, args=(dict(body, utterances=[utterance('u'+str(n))]),)) for n in range(2, 12)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(11, len(m.load('s')['utterances']))
            old = dict(body, session_id='old', started_at=(dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=31)).isoformat())
            m.sync(old); m.cleanup()
            self.assertFalse(os.path.exists(m.path('old')))
            forever = Meetings(os.path.join(scratch, 'kept'))
            forever.sync(old); forever.cleanup()
            self.assertTrue(os.path.exists(forever.path('old')))  # default: kept until deleted
            self.assertFalse(any(n.endswith('.tmp') for n in os.listdir(m.dir)))
    def test_http_auth_idempotence_open_search_history_conflict_and_delete(self):
        with tempfile.TemporaryDirectory() as scratch:
            backend = Backend.__new__(Backend)
            backend.jev = None
            backend.meetings = Meetings(os.path.join(scratch, 'meetings'))
            class Protected(Handler):
                api_token = 'test-full-key'
                enroll_secret = 'test-invite-secret'
            Protected.backend = backend
            server = ThreadingHTTPServer(('127.0.0.1', 0), Protected)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            invitation = issue_invite('test-invite-secret', TEAM[0], time.time()+60)
            def request(path, body=None, token='test-full-key', expected=200):
                headers = {'Authorization':'Bearer '+token} if token else {}
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(body).encode() if body is not None else None, headers=headers)
                try:
                    with urllib.request.urlopen(req) as r: status, result = r.status, json.load(r)
                except urllib.error.HTTPError as e:
                    status, result = e.code, json.load(e); e.close()
                self.assertEqual(expected, status, result)
                return result
            try:
                paths = [('/v1/notices', {}), ('/v1/meetings/sync', {}), ('/v1/meetings', None), ('/v1/meetings/s', None), ('/v1/meetings/delete', {}), ('/v1/meetings/search', {})]
                for path, body in paths:
                    request(path, body, None, 401); request(path, body, invitation, 403)
                body = dict(session_id='s', started_at=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone='UTC', utterances=[dict(utterance(), pcm='must-not-store', speaker_preview={'secret':'never-store'})])
                request('/v1/meetings/sync', body); request('/v1/meetings/sync', body)
                request('/v1/meetings/sync', dict(body, utterances=[utterance(tag='decision')]))
                opened = request('/v1/meetings/s')
                self.assertEqual(1, len(opened['digest']['decision'])); self.assertNotIn('pcm', opened['utterances'][0]); self.assertNotIn('speaker_preview', opened['utterances'][0])
                self.assertEqual(1, request('/v1/meetings?limit=10')['count'])
                self.assertEqual(1, len(request('/v1/meetings/search', {'query':'预算上限是多少'})['items']))
                self.assertEqual([], request('/v1/meetings/search', {'query':'周末天气怎样'})['items'])
                conflict = request('/v1/notices', dict(kind='conflict', session_id='next', utterance=utterance('u3', '预算大概要13万'), previous=[]))
                self.assertTrue(conflict['conflict']); self.assertEqual('h:s:u1', conflict['with']['id'])
                request('/v1/meetings?limit=999', expected=400)
                request('/v1/meetings/sync', dict(body, session_id='../bad'), expected=400)
                request('/v1/meetings/delete', {'session_id':'s'}, expected=400)
                request('/v1/meetings/delete', {'session_id':'s', 'confirm':True})
                request('/v1/meetings/s', expected=404)
                self.assertEqual(0, request('/v1/meetings')['count'])
                request('/v1/meetings/sync', body, expected=400)
                self.assertEqual(0, request('/v1/meetings')['count'])
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_reviews_survive_sync_and_clear_field_by_field(self):
        with tempfile.TemporaryDirectory() as scratch:
            m = Meetings(scratch, team=TEAM)
            body = dict(session_id='s', started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                        utterances=[dict(utterance(tag='decision'), speaker=None)])
            m.sync(body)
            m.review(dict(session_id='s', utterance_id='u1', speaker=TEAM[1], by=TEAM[0]))
            m.review(dict(session_id='s', utterance_id='u1', verdict='confirmed'))
            m.sync(dict(body, utterances=[dict(utterance(tag='decision'), speaker=TEAM[2])]))  # late model result
            row = m.load('s')
            self.assertEqual(TEAM[2], row['utterances'][0]['speaker'])  # model field kept as sent
            self.assertEqual(TEAM[1], row['utterances'][0]['review']['speaker'])
            self.assertEqual('confirmed', row['digest']['decision'][0]['review']['verdict'])
            self.assertNotIn('review', read_raw(m, 's')['utterances'][0])  # overlay is never persisted
            m.review(dict(session_id='s', utterance_id='u1', speaker=None))
            self.assertEqual({'verdict'}, set(m.load('s')['utterances'][0]['review']) - {'by', 'at'})
            m.review(dict(session_id='s', utterance_id='u1', verdict=None))
            self.assertNotIn('review', m.load('s')['utterances'][0])
            self.assertEqual({}, read_raw(m, 's')['reviews'])
            m.review(dict(session_id='s', utterance_id='u1', speaker='multi'))
            for bad in (dict(speaker='路人甲'), dict(owner='multi'), dict(verdict='maybe'), dict(utterance_id='nope', speaker=TEAM[0]), {}):
                with self.assertRaises(ValueError):
                    m.review(dict(dict(session_id='s', utterance_id='u1'), **bad))
            with self.assertRaises(FileNotFoundError):
                m.review(dict(session_id='missing', utterance_id='u1', speaker=TEAM[0]))
            m.delete(dict(session_id='s', confirm=True))
            with self.assertRaises(FileNotFoundError):
                m.review(dict(session_id='s', utterance_id='u1', speaker=TEAM[0]))

    def test_review_route_and_manual_speaker_beats_trial_split(self):
        with tempfile.TemporaryDirectory() as scratch:
            backend = Backend.__new__(Backend)
            backend.jev = None
            backend.speaker_state_lock = threading.RLock()
            backend.meetings = Meetings(os.path.join(scratch, 'meetings'), team=TEAM)
            backend.meetings.sync(dict(session_id='s', started_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                                       utterances=[utterance(text='我明天把方案发出来。', tag='commitment')]))
            said = dict(id='u1', text='我明天把方案发出来。', speaker=TEAM[0])
            backend.remember_trial(dict(session_id='s', utterance_id='u1', text=said['text']),
                                   [dict(text=said['text'], speaker=TEAM[3], overlap=False)])
            self.assertEqual(TEAM[3], backend.clause_speaker('s', said, 0))
            class Protected(Handler):
                api_token = 'test-full-key'
            Protected.backend = backend
            server = ThreadingHTTPServer(('127.0.0.1', 0), Protected)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            def post(body, token='test-full-key'):
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/v1/meetings/review',
                                             data=json.dumps(body).encode(), headers={'Authorization': 'Bearer ' + token})
                try:
                    with urllib.request.urlopen(req) as r: return r.status, json.load(r)
                except urllib.error.HTTPError as e:
                    result = (e.code, json.load(e)); e.close(); return result
            try:
                status, result = post(dict(session_id='s', utterance_id='u1', speaker=TEAM[1]))
                self.assertEqual(200, status, result)
                self.assertEqual(TEAM[1], result['review']['speaker'])
                self.assertEqual(TEAM[1], backend.clause_speaker('s', said, 0))
                self.assertIn(TEAM[1], backend._state('session_names')['s'])
                post(dict(session_id='s', utterance_id='u1', speaker='unclear'))
                self.assertIsNone(backend.clause_speaker('s', said, 0))  # not a person: no owner suggestion
                post(dict(session_id='s', utterance_id='u1', speaker=None))
                self.assertEqual(TEAM[3], backend.clause_speaker('s', said, 0))  # cleared: model again
                self.assertEqual(401, post(dict(session_id='s', utterance_id='u1', speaker=TEAM[1]), 'wrong')[0])
                self.assertEqual(404, post(dict(session_id='gone', utterance_id='u1', speaker=TEAM[1]))[0])
                self.assertEqual(400, post(dict(session_id='s', utterance_id='u1', speaker='路人甲'))[0])
                import hashlib
                from recordings import Recordings
                backend.recordings = Recordings(os.path.join(scratch, 'rec'))
                pcm = b'\x01\x00' * 32000
                backend.recordings.manifest('session-s', {'files': [{'name': 'audio.pcm', 'size': len(pcm), 'sha256': hashlib.sha256(pcm).hexdigest()}]})
                backend.recordings.chunk('session-s', 'audio.pcm', 0, pcm, hashlib.sha256(pcm).hexdigest())
                base = f'http://127.0.0.1:{server.server_port}'
                auth = {'Authorization': 'Bearer test-full-key'}
                with urllib.request.urlopen(urllib.request.Request(base + '/v1/recordings/session-s/clip?start_ms=100&end_ms=600', headers=auth)) as r:
                    self.assertEqual('audio/wav', r.headers['Content-Type'])
                    self.assertEqual(b'RIFF', r.read(4))
                for path, code in (('/v1/recordings/session-s/clip?start_ms=x&end_ms=600', 400), ('/v1/recordings/session-s/clip?start_ms=100&end_ms=9000', 409)):
                    with self.assertRaises(urllib.error.HTTPError) as failed:
                        urllib.request.urlopen(urllib.request.Request(base + path, headers=auth))
                    self.assertEqual(code, failed.exception.code); failed.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as unauthorized:
                    urllib.request.urlopen(base + '/v1/recordings/session-s/clip?start_ms=100&end_ms=600')
                self.assertEqual(401, unauthorized.exception.code); unauthorized.exception.close()
            finally:
                server.shutdown(); server.server_close(); worker.join()

    def test_tasks_join_feishu_state_and_cache_it(self):
        with tempfile.TemporaryDirectory() as scratch:
            backend = Backend.__new__(Backend)
            backend.args = types.SimpleNamespace(feishu='live')
            backend.lark = 'lark-cli'
            backend.people = types.SimpleNamespace(data={'self': {'name': TEAM[3], 'open_id': 'ou_me'},
                                                         'people': {TEAM[2]: 'ou_hong', TEAM[3]: 'ou_me'}})
            backend.ops = Operations(os.path.join(scratch, 'operations'))
            backend.meetings = Meetings(os.path.join(scratch, 'meetings'), backend.ops, team=TEAM)
            backend.meetings.sync(dict(session_id='s', started_at='2026-10-08T02:00:00+00:00',
                                       agenda=dict(goal='上线方案', items=[]), utterances=[utterance()]))
            def op(op_id, who, guid, created):
                backend.ops.save(dict(op_id=op_id, status='created', created_at=created, feishu_task={'guid': guid, 'url': 'u-' + guid},
                                      request=dict(session_id='s', draft=dict(who=who, what='发方案'), evidence=dict(quote='原话', utterance_ids=['u1']))))
            op('op-open-0001', TEAM[2], 'g-open', '2026-10-08T10:00:00+08:00')
            op('op-done-0001', TEAM[3], 'g-done', '2026-10-08T11:00:00+08:00')
            op('op-gone-0001', TEAM[2], 'g-gone', '2026-10-08T12:00:00+08:00')
            op('op-flaky-001', TEAM[0], 'g-flaky', '2026-10-08T13:00:00+08:00')
            backend.ops.save(dict(op_id='op-failed-001', status='failed', feishu_task=None, request={}))
            calls = []
            def fake_lark(lark, args, timeout=20):
                guid = json.loads(args[args.index('--params') + 1])['task_guid']
                calls.append(guid)
                if guid == 'g-gone':
                    raise local_backend.LarkError('{"subtype": "not_found", "code": 1470404}')
                if guid == 'g-flaky':
                    raise local_backend.LarkError('network down')
                return {'ok': True, 'data': {'task': {'guid': guid, 'summary': '发方案', 'url': 'x',
                        'status': 'done' if guid == 'g-done' else 'todo',
                        'completed_at': '1791500000000' if guid == 'g-done' else '0',
                        'due': {'timestamp': '1791400000000', 'is_all_day': True},
                        'members': [{'id': 'ou_hong' if guid == 'g-open' else 'ou_me', 'role': 'assignee', 'type': 'user'}]}}}
            original = local_backend.run_lark
            local_backend.run_lark = fake_lark
            try:
                every = backend.tasks(now=1791450000)
                self.assertEqual(['op-flaky-001', 'op-gone-0001', 'op-done-0001', 'op-open-0001'], [i['op_id'] for i in every['items']])
                by_id = {i['op_id']: i for i in every['items']}
                self.assertEqual('open', by_id['op-open-0001']['feishu']['state'])
                self.assertTrue(by_id['op-open-0001']['overdue'])
                self.assertEqual(TEAM[2], by_id['op-open-0001']['who'])
                self.assertEqual('上线方案', by_id['op-open-0001']['meeting']['goal'])
                self.assertEqual('done', by_id['op-done-0001']['feishu']['state'])
                self.assertFalse(by_id['op-done-0001']['overdue'])
                self.assertEqual('deleted', by_id['op-gone-0001']['feishu']['state'])
                self.assertEqual({'state': 'unknown', 'stale': True}, by_id['op-flaky-001']['feishu'])
                self.assertEqual(['op-flaky-001', 'op-open-0001'], [i['op_id'] for i in backend.tasks(status='open')['items']])
                self.assertEqual(['op-gone-0001', 'op-open-0001'], [i['op_id'] for i in backend.tasks(assignee=TEAM[2])['items']])
                calls.clear(); backend.tasks()
                self.assertEqual(['g-flaky'], calls)  # good and deleted states are cached; failures are retried
                for bad in (dict(status='late'), dict(assignee='路人甲')):
                    with self.assertRaises(local_backend.ApiError):
                        backend.tasks(**bad)
                backend.args.feishu = 'dry-run'
                backend._state('feishu_task_states').clear()
                self.assertEqual('unknown', backend.tasks()['items'][0]['feishu']['state'])
            finally:
                local_backend.run_lark = original


def read_raw(meetings, sid):
    with open(meetings.path(sid), encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__': unittest.main()
