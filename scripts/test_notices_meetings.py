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
            meetings = Meetings(scratch)
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
            m = Meetings(os.path.join(scratch, 'meetings'), ops)
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


if __name__ == '__main__': unittest.main()
