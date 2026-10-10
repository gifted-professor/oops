"""Oops bot flows against a fake lark-cli: no network, no Feishu writes."""
import base64
import datetime as dt
import json
import os
import sys
import tempfile
import time
import types

sys.path.insert(0, os.path.dirname(__file__))
import feishu_actions as fa  # noqa: E402
from meeting_features import Meetings  # noqa: E402

PEOPLE = {"self": {"name": "林春源", "open_id": "ou_lin"},
          "people": {"林春源": "ou_lin", "洪玉麟": "ou_hong", "江古月": "ou_jiang", "黄丽萍": "ou_huang"}}


class FakeResolver:
    def __init__(self, answers):
        self.answers = answers

    def complete_json(self, system, user, timeout=20):
        return (self.answers["analysis"] if "follow_ups" in system else self.answers["question"]), "fake"


def make(tmp, resolver):
    calls = []

    def run_lark(lark, args, timeout=20, cwd=None):
        calls.append(args)
        if args[:2] == ["docs", "+create"]:
            return {"ok": True, "data": {"document": {"document_id": "doxDOC", "url": "https://x.feishu.cn/docx/doxDOC"}}}
        if args[:2] == ["base", "+base-create"]:
            return {"ok": True, "data": {"base": {"app_token": "bascnBASE", "url": "https://x.feishu.cn/base/bascnBASE"}}}
        if args[:2] == ["calendar", "+create"]:
            return {"ok": True, "data": {"event": {"event_id": "evt1", "app_link": "https://applink/evt1"}}}
        if args[:2] == ["im", "+messages-send"]:
            return {"ok": True, "data": {"message_id": "om_1"}}
        return {"ok": True, "data": {}}

    meetings = Meetings(os.path.join(tmp, "meetings"), team=list(PEOPLE["people"]))
    ops = types.SimpleNamespace(load=lambda op_id: {"op_id": op_id, "request": {"draft": {"what": "交方案"}}})
    backend = types.SimpleNamespace(people=types.SimpleNamespace(data=PEOPLE), meetings=meetings, resolver=resolver, ops=ops)
    actions = fa.FeishuActions(backend, tmp, "/bin/lark-cli", run_lark, log=lambda m: None)
    return actions, meetings, calls


def utter(i, text, speaker, tag=None, start=None):
    start = start if start is not None else i * 10_000
    return dict(id=f"u{i}", text=text, start_ms=start, end_ms=start + 4000, speaker=speaker, **({"tag": tag} if tag else {}))


def test_after_meeting_and_buttons():
    tmp = tempfile.mkdtemp()
    future = (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))) + dt.timedelta(days=2)).strftime("%Y-%m-%dT15:00")
    analysis = {"substantive": True, "overview": "确定剪辑流程，周五交第一批样片。",
                "decisions": [{"text": "按现有剪辑流程执行", "ids": ["u0"]}, {"text": "编造的决定", "ids": ["u999"]}],
                "actions": [{"owner": "林春源", "task": "周五前把样片发给洪玉麟", "due": "周五前", "ids": ["u1"]},
                            {"owner": "外人", "task": "负责人不在团队里", "due": "", "ids": ["u1"]},
                            {"owner": "洪玉麟", "task": "没有出处", "due": "", "ids": []}],
                "topics": [{"topic": "预算", "points": ["预算不超过五千"], "ids": ["u2"]}],
                "follow_ups": [{"title": "样片评审", "start": future, "minutes": 45, "attendees": ["洪玉麟", "不存在"], "ids": ["u3"]},
                               {"title": "过去的会", "start": "2020-01-01T10:00", "minutes": 30, "attendees": [], "ids": ["u3"]},
                               {"title": "编造原话", "start": future, "minutes": 30, "attendees": [], "ids": ["u999"]}],
                "open_questions": [{"question": "抖音横屏视频的推荐分辨率是多少", "ids": ["u4"]}]}
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": analysis}))
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()  # after the bot's "since"
    meetings.sync(dict(session_id="s1", started_at=started, time_zone="Asia/Shanghai", utterances=[
        utter(0, "我们就按这个剪辑流程走", "林春源", "decision"),
        utter(1, "春源周五之前把样片发我", "洪玉麟", "commitment"),
        utter(2, "预算不能超过五千", "林春源", "constraint"),
        utter(3, "那周五下午三点再对一下", "洪玉麟"),
        utter(4, "抖音横屏到底推荐多大分辨率？", "林春源"),
        utter(5, "不知道，回头查", "洪玉麟")]))
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    meetings.sync(dict(session_id="s1", started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(), utterances=[]))
    actions.after_meeting(meetings.load("s1"))
    sent = [c for c in calls if c[:2] == ["im", "+messages-send"]]
    assert {c[c.index("--user-id") + 1] for c in sent} == {"ou_lin", "ou_hong"}, sent
    body = json.loads(sent[0][sent[0].index("--content") + 1])
    assert body["schema"] == "2.0"
    text = json.dumps(body, ensure_ascii=False)
    assert "样片评审" in text and "帮我查" in text and "打开纪要" in text and "过去的会" not in text and "编造原话" not in text
    assert "编造的决定" not in text and "没有出处" not in text and "春源周五之前" not in text  # model items only, never raw lines
    doc = next(c for c in calls if c[:2] == ["docs", "+create"])
    minutes = doc[doc.index("--content") + 1]
    assert "## 决定" in minutes and "**林春源**：周五前把样片发给洪玉麟（周五前）" in minutes and "负责人不在团队里（负责人未定）" in minutes
    assert "## 讨论要点" in minutes and "约束" not in minutes and "编造" not in minutes
    assert any(c[:2] == ["docs", "+create"] for c in calls)
    shared = [c for c in calls if c[:2] == ["api", "POST"] and "doxDOC" in c[2]]
    assert len(shared) == 2
    tables = [c for c in calls if c[:2] == ["base", "+record-batch-create"]]
    assert [c[c.index("--table-id") + 1] for c in tables] == ["会议", "决定", "待办"]
    todo_rows = json.loads(tables[2][tables[2].index("--json") + 1])["rows"]
    assert [r[:3] for r in todo_rows] == [["周五前把样片发给洪玉麟", "林春源", "周五前"], ["负责人不在团队里", "", ""]]
    # Done once: a second pass sends nothing more.
    n = len(calls)
    actions.tick(now=later)
    time.sleep(0.2)
    assert len(calls) == n

    # 建日程 button: attendees are the clicker and the people named for the follow-up; done once.
    actions.on_card({"event_id": "e1", "operator_id": "ou_hong", "action_value": json.dumps({"a": "calendar", "sid": "s1", "i": 0})})
    created = [c for c in calls if c[:2] == ["calendar", "+create"]]
    assert len(created) == 1 and set(created[0][created[0].index("--attendee-ids") + 1].split(",")) == {"ou_hong"}
    actions.on_card({"event_id": "e1", "operator_id": "ou_hong", "action_value": json.dumps({"a": "calendar", "sid": "s1", "i": 0})})
    actions.on_card({"event_id": "e2", "operator_id": "ou_lin", "action_value": json.dumps({"a": "calendar", "sid": "s1", "i": 0})})
    assert len([c for c in calls if c[:2] == ["calendar", "+create"]]) == 1

    # 帮我查 → a job for the Mac helper; the helper takes it, uploads a file and reports back.
    actions.on_card({"event_id": "e3", "operator_id": "ou_lin", "action_value": json.dumps({"a": "research", "sid": "s1", "i": 0})})
    job = actions.agent_call("poll", {"agent": "test", "wait": 0})["job"]
    assert job["kind"] == "research" and "分辨率" in job["payload"]["question"]
    assert actions.agent_call("poll", {"agent": "test", "wait": 0})["job"] is None
    data = b"PK fake pptx"
    actions.agent_call("chunk", {"name": "结果.pptx", "offset": 0, "data": base64.b64encode(data[:4]).decode()}, job["id"])
    actions.agent_call("chunk", {"name": "结果.pptx", "offset": 4, "data": base64.b64encode(data[4:]).decode()}, job["id"])
    try:
        actions.agent_call("chunk", {"name": "结果.pptx", "offset": 0, "data": "AA=="}, job["id"])
        raise AssertionError("wrong offset accepted")
    except ValueError:
        pass
    calls.clear()
    out = actions.agent_call("result", {"ok": True, "title": "分辨率", "markdown": "# 结论\n1080×1920", "brief": "推荐 1080p"}, job["id"])
    assert out["status"] == "done" and out["doc_url"]
    assert any("--file" in c for c in calls) and any(c[:2] == ["docs", "+create"] for c in calls)
    try:
        actions.agent_call("result", {"ok": True}, job["id"])
        raise AssertionError("finished job accepted twice")
    except ValueError:
        pass


def test_unanswered_question_becomes_research():
    tmp = tempfile.mkdtemp()
    actions, meetings, calls = make(tmp, FakeResolver({"question": {"answered": False, "worth": True, "question": "A 和 B 哪个便宜"}}))
    meetings.sync(dict(session_id="s2", started_at=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone="Asia/Shanghai", utterances=[
        utter(0, "A 和 B 到底哪个更便宜？", "洪玉麟", start=0),
        utter(1, "脸怎么了？", "林春源", start=170_000),
        utter(2, "我们先说下一个", "林春源", start=200_000)]))
    actions.scan_questions(meetings.load("s2"))
    jobs = actions.jobs.all()
    assert len(jobs) == 1 and jobs[0]["payload"]["auto"] and set(jobs[0]["requesters"]) == {"ou_lin", "ou_hong"}
    # The late question has not waited three minutes yet; nothing is asked twice.
    actions.scan_questions(meetings.load("s2"))
    assert len(actions.jobs.all()) == 1


def test_task_notice():
    tmp = tempfile.mkdtemp()
    actions, _, calls = make(tmp, None)
    op = {"op_id": "op1", "feishu_task": {"guid": "g", "url": "https://applink/task"},
          "request": {"draft": {"who": "洪玉麟", "what": "周五前交样片", "when": {"raw": "周五"}}, "evidence": {"quote": "周五之前把样片发我"}}}
    actions.task_created(op)
    time.sleep(0.2)
    sent = [c for c in calls if c[:2] == ["im", "+messages-send"]]
    assert len(sent) == 1 and sent[0][sent[0].index("--user-id") + 1] == "ou_hong"
    actions.task_created(op)
    time.sleep(0.2)
    assert len([c for c in calls if c[:2] == ["im", "+messages-send"]]) == 1


def test_nothing_worth_sending():
    tmp = tempfile.mkdtemp()
    empty = {"substantive": False, "overview": "", "decisions": [], "actions": [], "topics": [], "open_questions": [], "follow_ups": []}
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": empty}))
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
    meetings.sync(dict(session_id="s3", started_at=started, ended_at=started, time_zone="Asia/Shanghai",
                       utterances=[utter(i, "喂喂，听得到吗", "林春源") for i in range(6)]))
    actions.after_meeting(meetings.load("s3"))
    assert calls == [] and actions.state.get("meetings", "s3", "done")


if __name__ == "__main__":
    test_after_meeting_and_buttons()
    test_unanswered_question_becomes_research()
    test_task_notice()
    test_nothing_worth_sending()
    print("feishu actions ok")
