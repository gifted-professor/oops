#!/usr/bin/env python3
"""Bounded Jev calibration with synthetic Chinese text. Not a meeting accuracy assessment."""
import argparse
import json
import pathlib
import time
import types
from local_backend import load_jev, overlap
from meeting_features import judge_notice


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', default='.jev.env')
    p.add_argument('--model', default='jev-latest')
    p.add_argument('--output', default='evidence/notice-probe.json')
    a = p.parse_args()
    jev = load_jev(types.SimpleNamespace(jev_env=a.env_file, jev_model=a.model, no_jev=False))
    if jev is None: raise SystemExit('未配置 Jev 凭据，未运行探针')
    def u(uid, text, tag=None): return dict(id=uid, text=text, start_ms=0, end_ms=1000, tag=tag)
    ag = dict(goal='确定产品上线方案、预算和负责人', items=[dict(id='a1', title='方案评审'), dict(id='a2', title='预算确认')])
    cases = [
        ('topic-on', 'a2', dict(kind='topic', agenda=ag, utterances=[u('u1', '上线预算不能超过10万。'), u('u2', '这个方案需要8万，可以留2万作为应急预算。'), u('u3', '我们确认用8万的方案。')])),
        ('topic-off', 'off_topic', dict(kind='topic', agenda=ag, utterances=[u('u1', '昨天足球赛的比分是多少？'), u('u2', '下半场那个进球很精彩。'), u('u3', '聊聊下一轮谁会赢。')])),
        ('resolution-yes', True, dict(kind='resolution', item=ag['items'][0], utterances=[u('u1', '我们决定按方案B上线。', 'decision'), u('u2', '洪玉麟负责周五前提交新方案。', 'commitment')])),
        ('resolution-no', False, dict(kind='resolution', item=ag['items'][0], utterances=[u('u1', '方案A和方案B各有优点。'), u('u2', '目前还没决定，继续讨论。')])),
        ('conflict-u3-u1', True, dict(kind='conflict', utterance=u('u3', '这个想法不错，不过方案A要是上线，大概要13万。', 'decision'), previous=[u('u1', '我们这次要在两周内上线，预算不能超过10万。', 'constraint')]))]
    reports = []
    for name, expected, req in cases:
        start = time.monotonic()
        try:
            result = judge_notice(req, jev, overlap)
            actual = result.get('topic') if req['kind'] == 'topic' else result.get('resolved') if req['kind'] == 'resolution' else result.get('conflict')
            reports.append(dict(case=name, expected=expected, actual=actual, matched=actual == expected,
                latency_ms=round((time.monotonic()-start)*1000), result=result))
        except Exception as error:
            reports.append(dict(case=name, error=type(error).__name__, latency_ms=round((time.monotonic()-start)*1000)))
    report = dict(input_kind='synthetic Chinese examples only', accuracy_claim=False, reports=reports)
    path = pathlib.Path(a.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(report, ensure_ascii=False))
    if any('error' in r for r in reports): raise SystemExit(1)


if __name__ == '__main__': main()
