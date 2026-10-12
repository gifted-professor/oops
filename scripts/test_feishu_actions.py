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
from meeting_features import Meetings, read_json  # noqa: E402

PEOPLE = {"self": {"name": "林春源", "open_id": "ou_lin"},
          "people": {"林春源": "ou_lin", "洪玉麟": "ou_hong", "江古月": "ou_jiang", "黄丽萍": "ou_huang"}}
EMPTY = {"substantive": False, "overview": "", "decisions": [], "actions": [], "topics": [], "open_questions": [], "follow_ups": []}


class FakeResolver:
    def __init__(self, answers):
        self.answers = answers
        self.asked = 0

    def complete_json(self, system, user, timeout=20):
        self.asked += 1
        answer = self.answers["analysis"] if "follow_ups" in system else self.answers["question"]
        if isinstance(answer, Exception):
            raise answer
        return answer, "fake"


def make(tmp, resolver, fail=None, ops=None):
    """fail(args) → an exception to raise for that lark-cli call, or None."""
    calls = []

    def run_lark(lark, args, timeout=20, cwd=None):
        error = fail(args) if fail else None
        if error:
            raise error
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
    ops = ops or types.SimpleNamespace(load=lambda op_id: {"op_id": op_id, "request": {"draft": {"what": "交方案"}},
                                                           "feishu_task": {"url": "https://applink/task"}})
    backend = types.SimpleNamespace(people=types.SimpleNamespace(data=PEOPLE), meetings=meetings, resolver=resolver, ops=ops)
    logs = []
    actions = fa.FeishuActions(backend, tmp, "/bin/lark-cli", run_lark, log=logs.append)
    actions.logs = logs
    return actions, meetings, calls


def utter(i, text, speaker, tag=None, start=None):
    start = start if start is not None else i * 10_000
    return dict(id=f"u{i}", text=text, start_ms=start, end_ms=start + 4000, speaker=speaker, **({"tag": tag} if tag else {}))


def wait_for(check, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting")


def click(actions, event_id, user, value):
    thread = actions.on_card({"event_id": event_id, "operator_id": user, "action_value": json.dumps(value)})
    if thread:
        thread.join(5)


def sent_to(calls):
    return [c[c.index("--user-id") + 1] for c in calls if c[:2] == ["im", "+messages-send"]]


def texts(calls):
    return [c[c.index("--text") + 1] for c in calls if c[:2] == ["im", "+messages-send"] and "--text" in c]


def age(meetings, sid, minutes):
    """Pretend the last sentence of a meeting arrived some minutes ago."""
    row = read_json(meetings.path(sid))
    row["last_sync_at"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=minutes)).isoformat()
    meetings.write(sid, row)


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
        utter(5, "不知道，回头查", "洪玉麟"),
        utter(6, "江古月你听得到吗", "江古月"), utter(7, "好的", "江古月")]))  # two sentences: not counted as attending
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    meetings.sync(dict(session_id="s1", started_at=started, ended_at=dt.datetime.now(dt.timezone.utc).isoformat(), utterances=[]))
    actions.after_meeting(meetings.load("s1"))
    sent = [c for c in calls if c[:2] == ["im", "+messages-send"]]
    assert set(sent_to(calls)) == {"ou_lin", "ou_hong"}, sent
    body = json.loads(sent[0][sent[0].index("--content") + 1])
    assert body["schema"] == "2.0"
    text = json.dumps(body, ensure_ascii=False)
    assert "样片评审" in text and "帮我查" in text and "打开纪要" in text and "过去的会" not in text and "编造原话" not in text
    assert "编造的决定" not in text and "没有出处" not in text and "春源周五之前" not in text  # model items only, never raw lines
    doc = next(c for c in calls if c[:2] == ["docs", "+create"])
    minutes = doc[doc.index("--content") + 1]
    assert "## 决定" in minutes and "**林春源**：周五前把样片发给洪玉麟（周五前）" in minutes and "负责人不在团队里（负责人未定）" in minutes
    assert "## 讨论要点" in minutes and "约束" not in minutes and "编造" not in minutes
    assert "参会：林春源、洪玉麟" in minutes and "江古月" not in minutes
    shared = [c for c in calls if c[:2] == ["api", "POST"] and "doxDOC" in c[2]]
    assert len(shared) == 2
    tables = [c for c in calls if c[:2] == ["base", "+record-batch-create"]]
    assert [c[c.index("--table-id") + 1] for c in tables] == ["会议", "决定", "待办"]
    todo_rows = json.loads(tables[2][tables[2].index("--json") + 1])["rows"]
    assert [r[:3] for r in todo_rows] == [["周五前把样片发给洪玉麟", "林春源", "周五前"], ["负责人不在团队里", "", ""]]
    assert actions.state.get("meetings", "s1", "analysis", "open_questions")[0]["id"] == "u4"
    # Done once: a second pass sends nothing more.
    n = len(calls)
    actions.tick(now=later)
    time.sleep(0.2)
    assert len(calls) == n

    # 建日程 button: attendees are the clicker and the people named for the follow-up; done once.
    click(actions, "e1", "ou_hong", {"a": "calendar", "sid": "s1", "i": 0})
    created = [c for c in calls if c[:2] == ["calendar", "+create"]]
    assert len(created) == 1 and set(created[0][created[0].index("--attendee-ids") + 1].split(",")) == {"ou_hong"}
    click(actions, "e1", "ou_hong", {"a": "calendar", "sid": "s1", "i": 0})
    click(actions, "e2", "ou_lin", {"a": "calendar", "sid": "s1", "i": 0})
    assert len([c for c in calls if c[:2] == ["calendar", "+create"]]) == 1
    assert any("已经建好了：https://applink/evt1" in t for t in texts(calls))

    # 帮我查 → a job for the Mac helper; the helper takes it, uploads two files and reports back.
    click(actions, "e3", "ou_lin", {"a": "research", "sid": "s1", "i": 0})
    job = actions.agent_call("poll", {"agent": "test", "wait": 0})["job"]
    assert job["kind"] == "research" and "分辨率" in job["payload"]["question"]
    assert actions.jobs.load(job["id"])["key"] == "q-s1-u4"  # shared with an automatic look-up of the same sentence
    assert actions.agent_call("poll", {"agent": "test", "wait": 0})["job"] is None
    data = b"PK fake pptx"
    actions.agent_call("chunk", {"name": "结果.pptx", "offset": 0, "data": base64.b64encode(data[:4]).decode()}, job["id"])
    actions.agent_call("chunk", {"name": "结果.pptx", "offset": 4, "data": base64.b64encode(data[4:]).decode()}, job["id"])
    actions.agent_call("chunk", {"name": "附录.md", "offset": 0, "data": base64.b64encode(b"# x").decode()}, job["id"])
    try:
        actions.agent_call("chunk", {"name": "结果.pptx", "offset": 0, "data": "AA=="}, job["id"])
        raise AssertionError("wrong offset accepted")
    except ValueError:
        pass
    calls.clear()
    out = actions.agent_call("result", {"ok": True, "title": "分辨率", "markdown": "# 结论\n1080×1920", "brief": "推荐 1080p"}, job["id"])
    assert out["status"] == "delivering"
    try:
        actions.agent_call("result", {"ok": False, "error": "timeout"}, job["id"])
        raise AssertionError("finished job accepted twice")
    except ValueError:
        pass
    wait_for(lambda: actions.jobs.load(job["id"])["status"] == "done")
    files = [c for c in calls if "--file" in c]
    keys = {c[c.index("--idempotency-key") + 1] for c in files}
    assert len(files) == 2 and len(keys) == 2, files  # every file arrives, not just the first
    assert actions.jobs.load(job["id"])["doc_url"] and any(c[:2] == ["docs", "+create"] for c in calls)

    # Clicking again once it is done sends the result again instead of "already working on it".
    calls.clear()
    click(actions, "e4", "ou_hong", {"a": "research", "sid": "s1", "i": 0})
    assert sent_to(calls).count("ou_hong") == 3 and "ou_hong" in actions.jobs.load(job["id"])["requesters"]


def test_failed_job_is_reported_and_can_be_redone():
    tmp = tempfile.mkdtemp()
    actions, meetings, calls = make(tmp, None)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    meetings.sync(dict(session_id="s5", started_at=started, time_zone="Asia/Shanghai", utterances=[utter(0, "做个PPT", "林春源")]))
    click(actions, "e1", "ou_lin", {"a": "ppt", "sid": "s5"})
    job = actions.agent_call("poll", {"agent": "test", "wait": 0})["job"]
    calls.clear()
    assert actions.agent_call("result", {"ok": False, "error": "没有生成 slides.pptx"}, job["id"])["status"] == "failed"
    wait_for(lambda: texts(calls))
    assert "没有做成：没有生成 slides.pptx" in texts(calls)[0]
    click(actions, "e2", "ou_lin", {"a": "ppt", "sid": "s5"})
    again = actions.agent_call("poll", {"agent": "test", "wait": 0})["job"]
    assert again and again["id"] != job["id"]

    # A job whose last allowed attempt ran out of time is given up and its requester told.
    stuck = actions.jobs.load(again["id"])
    stuck.update(attempts=fa.JOB_ATTEMPTS, lease_until=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)).isoformat())
    actions.jobs.save(stuck)
    calls.clear()
    actions.tick()
    wait_for(lambda: texts(calls))
    assert actions.jobs.load(again["id"])["status"] == "failed" and "都没有做完" in texts(calls)[0]


def test_meeting_end_is_inferred_when_the_phone_never_says():
    tmp = tempfile.mkdtemp()
    analysis = dict(EMPTY, substantive=True, overview="定了样片", decisions=[{"text": "周五交样片", "ids": ["u0"]}])
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": analysis, "question": {"answered": True, "worth": False}}))
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
    rows = [utter(i, f"第{i}句关于样片", "林春源") for i in range(6)]
    for sid, count in (("quiet", 6), ("tiny", 3), ("live", 6), ("legacy", 6)):
        meetings.sync(dict(session_id=sid, started_at=started, time_zone="Asia/Shanghai", utterances=rows[:count]))
    assert read_json(meetings.path("live"))["last_sync_at"]
    age(meetings, "quiet", 21)
    age(meetings, "tiny", 21)
    age(meetings, "live", 5)
    legacy = read_json(meetings.path("legacy"))
    legacy.pop("last_sync_at")
    meetings.write("legacy", legacy)
    now = dt.datetime.now(dt.timezone.utc)
    assert fa.effective_end(meetings.load("quiet"), now)[1] == "idle"
    assert fa.effective_end(meetings.load("tiny"), now) == (None, None)
    assert fa.effective_end(meetings.load("live"), now) == (None, None)
    assert fa.effective_end(meetings.load("legacy"), now) == (None, None)
    actions.tick(now=now + dt.timedelta(minutes=5))  # the inferred end (start + last sentence) is 90 s behind
    wait_for(lambda: actions.state.get("meetings", "quiet", "done"))
    time.sleep(0.1)
    assert set(actions.state.get("meetings")) == {"quiet"}
    assert any("ended (idle)" in line for line in actions.logs)

    # An end time from the recording (or the phone) is used as it is.
    meetings.set_ended("live", now.isoformat(), "recording")
    assert not meetings.set_ended("live", (now + dt.timedelta(minutes=1)).isoformat(), "recording")
    assert fa.effective_end(meetings.load("live"), now) == (now, "recording")


def test_generated_title_is_waited_for():
    tmp = tempfile.mkdtemp()
    analysis = dict(EMPTY, substantive=True, decisions=[{"text": "周五交样片", "ids": ["u0"]}])
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": analysis, "question": {"answered": True, "worth": False}}))
    made = []

    def make_title(sid, count, transcript):
        made.append(sid)
        meetings.set_auto_title(sid, "样片排期", count, "fake")
    actions.backend.make_title = make_title
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
    meetings.sync(dict(session_id="s6", started_at=started, ended_at=started, time_zone="Asia/Shanghai",
                       utterances=[utter(i, f"第{i}句关于样片的讨论", "林春源") for i in range(6)]))
    actions.after_meeting_safe("s6")
    doc = next(c for c in calls if c[:2] == ["docs", "+create"])
    assert made == ["s6"] and doc[doc.index("--title") + 1] == "会议纪要 · 样片排期"


def test_ledger_failures_resume_without_duplicates():
    tmp = tempfile.mkdtemp()
    analysis = dict(EMPTY, substantive=True, decisions=[{"text": "周五交样片", "ids": ["u0"]}],
                    actions=[{"owner": "林春源", "task": "发样片", "due": "", "ids": ["u0"]}])
    broken = {"决定表": 1, "待办行": 1}

    def fail(args):
        if args[:2] == ["base", "+table-create"] and args[args.index("--name") + 1] == "决定" and broken["决定表"]:
            broken["决定表"] -= 1
            return RuntimeError("no permission")
        if args[:2] == ["base", "+record-batch-create"] and args[args.index("--table-id") + 1] == "待办" and broken["待办行"]:
            broken["待办行"] -= 1
            return RuntimeError("rate limited")
        return None
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": analysis}), fail=fail)
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
    meetings.sync(dict(session_id="s7", started_at=started, ended_at=started, time_zone="Asia/Shanghai",
                       utterances=[utter(i, f"第{i}句", "林春源") for i in range(6)]))
    actions.after_meeting(meetings.load("s7"))
    assert actions.state.get("meetings", "s7", "done") and set(sent_to(calls)) == {"ou_lin"}  # cards still went out
    assert actions.state.get("meetings", "s7", "ledger_pending")
    for _ in range(3):
        actions.ledger_safe("s7")
    assert actions.state.get("meetings", "s7", "ledger") and not actions.state.get("meetings", "s7", "ledger_pending")
    assert len([c for c in calls if c[:2] == ["base", "+base-create"]]) == 1
    created = [c[c.index("--name") + 1] for c in calls if c[:2] == ["base", "+table-create"]]
    assert sorted(created) == sorted(fa.LEDGER_TABLES)
    written = [c[c.index("--table-id") + 1] for c in calls if c[:2] == ["base", "+record-batch-create"]]
    assert sorted(written) == ["会议", "决定", "待办"], written  # each table once, though two attempts failed
    # The loop only retries the ledger, never the cards.
    actions.state.put(True, "meetings", "s7", "ledger_pending")
    actions.state.put(True, "meetings", "s7", "ledger_tables", "待办")
    n = len(sent_to(calls))
    actions.tick(now=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    wait_for(lambda: not actions.state.get("meetings", "s7", "ledger_pending"))
    assert len(sent_to(calls)) == n


def test_unanswered_question_becomes_research():
    tmp = tempfile.mkdtemp()
    resolver = FakeResolver({"question": {"answered": False, "worth": True, "question": "A 和 B 哪个便宜"}})
    actions, meetings, calls = make(tmp, resolver)
    meetings.sync(dict(session_id="s2", started_at=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone="Asia/Shanghai", utterances=[
        utter(0, "A 和 B 到底哪个更便宜？", "洪玉麟", start=0),
        utter(1, "那个镜头里的脸怎么了？", "林春源", start=170_000),
        utter(2, "我们先说下一个", "林春源", start=200_000)]))
    assert actions.scan_questions(meetings.load("s2"))
    jobs = actions.jobs.all()
    assert len(jobs) == 1 and jobs[0]["payload"]["auto"] and set(jobs[0]["requesters"]) == {"ou_lin", "ou_hong"}
    # The late question has not waited three minutes yet; nothing is asked twice.
    actions.scan_questions(meetings.load("s2"))
    assert len(actions.jobs.all()) == 1
    # Once the meeting is over, the last minutes' questions are judged too.
    assert actions.scan_questions(meetings.load("s2"), final=True)
    assert len(actions.jobs.all()) == 2 and actions.state.get("questions", "s2", "u1") == "research"


def test_question_check_backs_off_after_an_error():
    tmp = tempfile.mkdtemp()
    resolver = FakeResolver({"question": TimeoutError("deepseek slow")})
    actions, meetings, _ = make(tmp, resolver)
    meetings.sync(dict(session_id="s8", started_at=dt.datetime.now(dt.timezone.utc).isoformat(), time_zone="Asia/Shanghai", utterances=[
        utter(0, "这个接口到底支持多少并发？", "洪玉麟", start=0), utter(1, "下一个", "林春源", start=200_000)]))
    assert not actions.scan_questions(meetings.load("s8"))
    assert not actions.scan_questions(meetings.load("s8"))
    assert resolver.asked == 1 and actions.state.get("questions", "s8") is None


def test_task_notice():
    tmp = tempfile.mkdtemp()
    actions, _, calls = make(tmp, None)
    op = {"op_id": "op1", "feishu_task": {"guid": "g", "url": "https://applink/task"},
          "request": {"draft": {"who": "洪玉麟", "what": "周五前交样片", "when": {"raw": "周五"}}, "evidence": {"quote": "周五之前把样片发我"}}}
    actions.task_created(op)
    wait_for(lambda: sent_to(calls))
    assert sent_to(calls) == ["ou_hong"]
    actions.task_created(op)
    time.sleep(0.2)
    assert len(sent_to(calls)) == 1
    # Not in the directory: the task sits under the account owner, who is told so.
    stranger = dict(op, op_id="op2", request=dict(op["request"], draft=dict(op["request"]["draft"], who="Jason")))
    actions.task_created(stranger)
    wait_for(lambda: len(sent_to(calls)) == 2)
    assert sent_to(calls)[1] == "ou_lin" and "Jason不在飞书通讯录里" in calls[-1][calls[-1].index("--content") + 1]


def test_buttons_always_answer():
    tmp = tempfile.mkdtemp()
    ops = types.SimpleNamespace(load=lambda op_id: None if op_id == "gone" else
                                {"op_id": op_id, "request": {"draft": {"what": "交方案"}}, "feishu_task": {"url": "https://applink/task"}})
    actions, meetings, calls = make(tmp, None, ops=ops)
    click(actions, "e1", "ou_hong", {"a": "not_me", "op": "op1"})
    assert sent_to(calls) == ["ou_lin", "ou_hong"] and "https://applink/task" in texts(calls)[0]
    calls.clear()
    click(actions, "e2", "ou_hong", {"a": "not_me", "op": "gone"})
    assert texts(calls) == ["找不到这条任务，可能已经删除了。"]
    calls.clear()
    click(actions, "e3", "ou_hong", {"a": "report", "sid": "deleted-meeting"})
    assert "没做成：这场会议已被删除" in texts(calls)[0]


def test_nothing_worth_sending():
    tmp = tempfile.mkdtemp()
    actions, meetings, calls = make(tmp, FakeResolver({"analysis": EMPTY}))
    started = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=5)).isoformat()
    meetings.sync(dict(session_id="s3", started_at=started, ended_at=started, time_zone="Asia/Shanghai",
                       utterances=[utter(i, "喂喂，听得到吗", "林春源") for i in range(6)]))
    actions.after_meeting(meetings.load("s3"))
    assert calls == [] and actions.state.get("meetings", "s3", "done")


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_"):
            test()
    print("feishu actions ok")
