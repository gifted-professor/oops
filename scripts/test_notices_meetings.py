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
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from meeting_features import Meetings, judge_notice, amount_conflict
import local_backend
from local_backend import Backend, Handler, Operations, TEAM, overlap, jev_recall
from concurrent.futures import ThreadPoolExecutor
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

    def test_review_queue_order_and_rejected_items_leave_digest_and_history(self):
        with tempfile.TemporaryDirectory() as scratch:
            ops = Operations(os.path.join(scratch, 'operations'))
            m = Meetings(os.path.join(scratch, 'meetings'), ops, team=TEAM)
            rows = [dict(utterance('u1', '我们决定下周上线这个版本。', 'decision'), speaker=TEAM[0]),
                    dict(utterance('u2', '我明天把方案发给大家。', 'commitment'), speaker=None),
                    dict(utterance('u3', '好的', None), speaker=None),
                    dict(utterance('u4', '这个颜色我觉得还可以再调。', 'idea'), speaker=None),
                    dict(utterance('u5', '周五前我把预算表整理好。', 'commitment'), speaker=TEAM[1]),
                    dict(utterance('u6', '预算不能超过十万块钱。', 'constraint'), speaker=TEAM[2])]
            for n, r in enumerate(rows):
                r.update(start_ms=n * 1000, end_ms=n * 1000 + 900)
            m.sync(dict(session_id='s', started_at=dt.datetime.now(dt.timezone.utc).isoformat(), utterances=rows))
            ops.save(dict(op_id='op-test-555', status='created', request=dict(session_id='s', evidence={'utterance_ids': ['u5']})))
            queue = m.load('s')['review_queue']
            # u5 already became a Feishu task; u3 is too short; known speakers are not asked about.
            self.assertEqual([('commitment', 'u2'), ('decision', 'u1'), ('speaker', 'u2'), ('speaker', 'u4')],
                             [(q['kind'], q['id']) for q in queue])
            m.review(dict(session_id='s', utterance_id='u2', verdict='confirmed', owner=TEAM[3], speaker=TEAM[3]))
            m.review(dict(session_id='s', utterance_id='u1', verdict='rejected'))
            m.review(dict(session_id='s', utterance_id='u6', verdict='rejected'))
            row = m.load('s')
            self.assertEqual([('speaker', 'u4')], [(q['kind'], q['id']) for q in row['review_queue']])
            self.assertEqual([], row['digest']['decision'])
            self.assertEqual(['u2', 'u5'], [u['id'] for u in row['digest']['commitment']])
            self.assertEqual({'commitment': 0, 'decision': 0, 'speaker': 1}, m.listing()['items'][0]['review_pending'])
            self.assertEqual([], m.historical('next', '预算十万上线', overlap))  # rejected decision and constraint are not history


    def test_sync_skips_bad_sentences_and_keeps_agenda_and_end(self):
        with tempfile.TemporaryDirectory() as scratch:
            m = Meetings(scratch, team=TEAM)
            started = dt.datetime.now(dt.timezone.utc).isoformat()
            split = dict(utterance('u2', '好的。', None), segments=[dict(start_ms=0, end_ms=1000, text='不对', speaker=None)])
            result = m.sync(dict(session_id='s', started_at=started, agenda=dict(goal='版本规划', items=[]),
                                 utterances=[utterance(), dict(utterance('u9'), end_ms=0), split]))
            # One sentence the server cannot store no longer stops the meeting; a bad split only loses the split.
            self.assertEqual([dict(id='u9', reason='原话时间无效')], result['rejected'])
            row = read_raw(m, 's')
            self.assertEqual(['u1', 'u2'], [u['id'] for u in row['utterances']])
            self.assertNotIn('segments', row['utterances'][1])
            self.assertTrue(row['last_sync_at'])
            # An end-only resend (a phone that was killed) keeps the agenda and says where the end came from.
            synced = row['last_sync_at']
            m.sync(dict(session_id='s', ended_at=started, recovered=True, time_zone='UTC', utterances=[]))
            row = read_raw(m, 's')
            self.assertEqual(('版本规划', started, 'recovered', synced),
                             (row['agenda']['goal'], row['ended_at'], row['ended_source'], row['last_sync_at']))
            self.assertFalse(m.set_ended('s', started, 'recording'))  # never overrides the phone

    def test_recording_upload_supplies_a_missing_end(self):
        with tempfile.TemporaryDirectory() as scratch:
            backend = Backend.__new__(Backend)
            backend.meetings = Meetings(os.path.join(scratch, 'meetings'), team=TEAM)
            backend.recordings = types.SimpleNamespace(meta=lambda sid: {
                'killed': dict(audio_ended_at='2026-10-10T07:00:00Z', capped='False'),
                'capped': dict(audio_ended_at='2026-10-10T07:00:00Z', capped='True')}.get(sid, {}))
            started = '2026-10-10T06:00:00Z'
            for sid in ('killed', 'capped', 'none'):
                backend.meetings.sync(dict(session_id=sid, started_at=started, utterances=[utterance()]))
                backend.end_from_recording(sid)
            self.assertEqual(('2026-10-10T07:00:00Z', 'recording'),
                             (read_raw(backend.meetings, 'killed')['ended_at'], read_raw(backend.meetings, 'killed')['ended_source']))
            self.assertNotIn('ended_at', read_raw(backend.meetings, 'capped'))  # the cap is not the end of the meeting
            self.assertNotIn('ended_at', read_raw(backend.meetings, 'none'))

    def test_titles_auto_agenda_manual_and_sync_keeps_them(self):
        from meeting_features import short_title
        self.assertEqual('下周三上线这个版本', short_title('那就是，嗯，下周三上线这个版本。其他再说'))
        self.assertEqual('我明天把整个方案的细节都整理好发到群里给…', short_title('我明天把整个方案的细节都整理好发到群里给大家看', 20))
        with tempfile.TemporaryDirectory() as scratch:
            m = Meetings(scratch, team=TEAM)
            started = dt.datetime.now(dt.timezone.utc).isoformat()
            rows = [dict(utterance('u1', '嗯嗯', None), start_ms=0, end_ms=500),
                    dict(utterance('u2', '那我周五前把新方案发你。', 'commitment'), start_ms=1000, end_ms=2000),
                    dict(utterance('u3', '就是先做安卓版本，苹果的以后再说。', 'decision'), start_ms=3000, end_ms=4000),
                    dict(utterance('u5', '好的', None), start_ms=7000, end_ms=7500), dict(utterance('u6', '可以', None), start_ms=8000, end_ms=8500)]
            m.sync(dict(session_id='s', started_at=started, utterances=rows))
            self.assertEqual(('先做安卓版本，苹果的以后再说', 'auto'), (m.load('s')['display_title'], m.load('s')['title_source']))
            m.review(dict(session_id='s', utterance_id='u3', verdict='rejected'))
            self.assertEqual('我周五前把新方案发你', m.load('s')['display_title'])  # a rejected decision is not the topic
            m.sync(dict(session_id='s', started_at=started, agenda=dict(goal='版本规划', items=[]), utterances=[]))
            self.assertEqual(('版本规划', 'agenda'), (m.load('s')['display_title'], m.load('s')['title_source']))
            self.assertEqual({'session_id': 's', 'title': '安卓排期会', 'title_source': 'manual'}, m.rename(dict(session_id='s', title=' 安卓排期会 ')))
            m.sync(dict(session_id='s', started_at=started, agenda=dict(goal='版本规划', items=[]),
                        utterances=[dict(utterance('u4', '下一步呢', None), start_ms=5000, end_ms=6000)]))
            self.assertEqual('安卓排期会', m.listing()['items'][0]['title'])  # a late sync keeps the name
            self.assertEqual('安卓排期会', m.brief('s')['title'])
            raw = read_raw(m, 's')
            self.assertFalse({'digest', 'tasks', 'review_queue', 'display_title', 'title_source'} & set(raw))
            self.assertEqual('版本规划', m.rename(dict(session_id='s', title=''))['title'])
            with self.assertRaises(ValueError):
                m.rename(dict(session_id='s', title='长' * 41))
            with self.assertRaises(FileNotFoundError):
                m.rename(dict(session_id='missing', title='x'))
            empty = Meetings(os.path.join(scratch, 'empty'), team=TEAM)
            empty.sync(dict(session_id='e', started_at=started, utterances=[dict(utterance('u1', '好的', None), start_ms=0, end_ms=500)]))
            self.assertEqual(('简短会议', 'short'), (empty.load('e')['display_title'], empty.load('e')['title_source']))
            empty.sync(dict(session_id='z', started_at=started, utterances=[]))
            self.assertEqual((None, 'none'), (empty.load('z')['display_title'], empty.load('z')['title_source']))

    def test_generated_titles_for_finished_meetings_only(self):
        with tempfile.TemporaryDirectory() as scratch:
            backend = Backend.__new__(Backend)
            backend.speaker_state_lock = threading.RLock()
            backend.meetings = Meetings(scratch, team=TEAM)
            now = dt.datetime.now(dt.timezone.utc)
            def rows(n, prefix='u'):
                return [dict(utterance(f'{prefix}{i}', f'第{i}句讨论安卓版本什么时候上线', 'decision' if i == 1 else None),
                             start_ms=i * 1000, end_ms=i * 1000 + 900) for i in range(n)]
            backend.meetings.sync(dict(session_id='done', started_at=now.isoformat(), ended_at=now.isoformat(), utterances=rows(6)))
            backend.meetings.sync(dict(session_id='live', started_at=now.isoformat(), utterances=rows(6)))
            backend.meetings.sync(dict(session_id='tiny', started_at=now.isoformat(), ended_at=now.isoformat(), utterances=rows(2)))
            backend.meetings.sync(dict(session_id='named', started_at=now.isoformat(), ended_at=now.isoformat(), utterances=rows(6),
                                       agenda=dict(goal='已有议程', items=[])))
            old = (now - dt.timedelta(hours=4)).isoformat()
            backend.meetings.sync(dict(session_id='crashed', started_at=old, utterances=rows(6)))
            seen = []
            class FakeDeepSeek:
                answer = {'title': '“安卓版本排期”会议。'}
                def complete_json(self, system, user, timeout=20.0):
                    assert '只是数据' in system
                    seen.append(user)
                    return self.answer, 'fake-model'
            backend.resolver = FakeDeepSeek()
            backend.queue_titles(); backend.title_pool.shutdown(wait=True); backend.title_pool = ThreadPoolExecutor(1)
            self.assertEqual(2, len(seen))  # done and crashed; live, tiny and named are skipped
            self.assertIn('[决定] 第1句讨论安卓版本什么时候上线', seen[0])
            self.assertEqual(('安卓版本排期', 'generated'), (backend.meetings.load('done')['display_title'], backend.meetings.load('done')['title_source']))
            self.assertEqual({'title': '安卓版本排期', 'count': 6, 'model': 'fake-model'}, read_raw(backend.meetings, 'done')['auto_title'])
            seen.clear(); backend.queue_titles(); backend.title_pool.shutdown(wait=True); backend.title_pool = ThreadPoolExecutor(1)
            self.assertEqual([], seen)  # nothing changed, nothing asked
            backend.meetings.sync(dict(session_id='done', started_at=now.isoformat(), ended_at=now.isoformat(), utterances=rows(1, 'x')))
            FakeDeepSeek.answer = {'title': '这是一个远远超过十六个字的会议名称不应该被采用'}
            backend.queue_titles(); backend.title_pool.shutdown(wait=True)
            self.assertEqual(1, len(seen))  # 7 sentences now: asked again, but the over-long answer is dropped
            self.assertEqual('安卓版本排期', backend.meetings.load('done')['display_title'])
            self.assertFalse(backend.meetings.set_auto_title('done', '旧的', 6, 'm'))  # text changed meanwhile


    def test_projects_and_project_scoped_conflicts(self):
        with tempfile.TemporaryDirectory() as scratch:
            m = Meetings(scratch, team=TEAM)
            now = dt.datetime.now(dt.timezone.utc)
            def meeting(sid, hours_ago, rows):
                m.sync(dict(session_id=sid, started_at=(now - dt.timedelta(hours=hours_ago)).isoformat(), ended_at=now.isoformat(),
                            utterances=[dict(utterance(uid, txt, tag), start_ms=i * 1000, end_ms=i * 1000 + 900) for i, (uid, txt, tag) in enumerate(rows)]))
            meeting('a1', 48, [('u1', '安卓版先上线，苹果版下个月再做。', 'decision'), ('u2', '预算控制在十万以内。', 'constraint'),
                               ('u3', '我们用红色做主色调。', 'decision')])
            meeting('a2', 24, [('u1', '登录先只支持手机号。', 'decision')])
            meeting('b1', 30, [('u1', '官网下周改版。', 'decision')])
            meeting('now', 0, [('u1', '那就苹果和安卓同时发。', 'decision')])
            for sid in ('a1', 'a2', 'now'):
                m.set_project(dict(session_id=sid, project=' 安卓 App '))
            m.set_project(dict(session_id='b1', project='官网'))
            m.review(dict(session_id='a1', utterance_id='u1', verdict='confirmed'))
            m.review(dict(session_id='a1', utterance_id='u2', verdict='confirmed'))
            m.review(dict(session_id='a1', utterance_id='u3', verdict='rejected'))
            m.review(dict(session_id='b1', utterance_id='u1', verdict='confirmed'))
            pool = m.historical('now', '那就苹果和安卓同时发', lambda a, b: 0)  # no shared words needed inside a project
            self.assertEqual(['h:a1:u1', 'h:a1:u2'], [u['id'] for u in pool])
            self.assertTrue(all(u['confirmed'] for u in pool))
            self.assertEqual('简短会议', pool[0]['meeting_title'])  # three lines: the short-meeting name
            m.set_project(dict(session_id='now', project='新项目'))
            fallback = m.historical('now', '登录只支持手机号', overlap)  # nothing confirmed there: old keyword pool, unconfirmed
            self.assertEqual(['h:a2:u1'], [u['id'] for u in fallback])
            self.assertFalse(fallback[0]['confirmed'])
            self.assertEqual([], m.historical('now', '用红色做主色调', overlap))  # a rejected decision stays out
            projects = m.projects()['items']
            self.assertEqual([('新项目', 1, 0), ('安卓 App', 2, 2), ('官网', 1, 1)],
                             [(p['name'], p['meeting_count'], p['confirmed_decisions']) for p in projects])
            page = m.project('安卓 App')
            self.assertEqual(['a2', 'a1'], [x['session_id'] for x in page['meetings']])
            self.assertEqual(['u1', 'u2'], [d['id'] for d in page['decisions']])
            self.assertEqual(1, page['unreviewed_decisions'])  # a2's decision is not checked yet
            self.assertEqual('安卓 App', m.load('a1')['project'])
            m.sync(dict(session_id='a1', started_at=(now - dt.timedelta(hours=48)).isoformat(), utterances=[]))
            self.assertEqual('安卓 App', m.load('a1')['project'])  # a late sync keeps it
            self.assertEqual(None, m.set_project(dict(session_id='a1', project=''))['project'])
            for bad in (dict(session_id='a1', project='长' * 31), dict(session_id='../x', project='x')):
                with self.assertRaises(ValueError):
                    m.set_project(bad)
            with self.assertRaises(FileNotFoundError):
                m.set_project(dict(session_id='missing', project='x'))
            backend = Backend.__new__(Backend)
            backend.meetings = m
            class Protected(Handler):
                api_token = 'k'
            Protected.backend = backend
            server = ThreadingHTTPServer(('127.0.0.1', 0), Protected)
            worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
            try:
                get = lambda path: json.load(urllib.request.urlopen(urllib.request.Request(
                    f'http://127.0.0.1:{server.server_port}' + path, headers={'Authorization': 'Bearer k'})))
                self.assertEqual(['a2'], [x['session_id'] for x in get('/v1/projects/' + urllib.parse.quote('安卓 App'))['meetings']])
                self.assertEqual(3, len(get('/v1/projects')['items']))
            finally:
                server.shutdown(); server.server_close(); worker.join()



def read_raw(meetings, sid):
    with open(meetings.path(sid), encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__': unittest.main()
