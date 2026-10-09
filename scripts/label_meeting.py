#!/usr/bin/env python3
"""Local labelling page for one archived meeting: who spoke each sentence, which sentences are tasks.

    label_meeting.py 22bec74d-f061-4978-8e8d-4b2959c1d0c8        # after archive_to_eval.py converted it
    → open http://127.0.0.1:8796/

The page is blind: it shows no model tag and no model speaker. Each row plays its own audio. Labels are saved
as you click, to evidence/<session>/labels.json, and evidence/<session>/truth.json (the format task_eval.py
reads, plus a `speakers` map) is rebuilt on every save. Listens on 127.0.0.1 only.
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PEOPLE = ["林春源", "洪玉麟", "江古月", "黄丽萍"]
SPEAKER_OTHER = {"other": "其他人", "multi": "多人同时", "unclear": "听不清"}
OWNER_OTHER = {"collective": "集体/大家", "unknown": "不确定"}
SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
RATE = 16000


def derive_truth(session, utterances, labels, now=None):
    """truth.json for task_eval.py from raw labels. Unlabelled sentences are simply absent from it."""
    known = {u["id"] for u in utterances}
    tasks, non_tasks, groups, speakers = [], [], {}, {}
    for u in utterances:
        label = labels.get(u["id"]) or {}
        if label.get("task") == "task":
            owner = label.get("owner")
            tasks.append({"utterance": u["id"], "phrase": u["text"][:40], "owner": owner if owner in PEOPLE else None,
                          **({"note": OWNER_OTHER[owner]} if owner in OWNER_OTHER else {})})
        elif label.get("task") == "not_task":
            non_tasks.append({"utterance": u["id"], "phrase": u["text"][:40]})
        if label.get("dup"):
            groups.setdefault(label["dup"], []).append(u["id"])
        if label.get("speaker") in PEOPLE or label.get("speaker") in SPEAKER_OTHER:
            speakers[u["id"]] = label["speaker"]
    assert all(t["utterance"] in known for t in tasks)
    return {"meeting": session, "labeled_at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"),
            "tasks": tasks, "non_tasks": non_tasks,
            "duplicates": [ids for _, ids in sorted(groups.items()) if len(ids) >= 2], "speakers": speakers}


def clean_labels(raw, utterances):
    """Only known ids and known values are kept; anything else is dropped rather than trusted."""
    known = {u["id"] for u in utterances}
    cleaned = {}
    for uid, label in (raw or {}).items():
        if uid not in known or not isinstance(label, dict):
            continue
        item = {}
        if label.get("speaker") in PEOPLE or label.get("speaker") in SPEAKER_OTHER:
            item["speaker"] = label["speaker"]
        if label.get("task") in ("task", "not_task"):
            item["task"] = label["task"]
        if item.get("task") == "task" and (label.get("owner") in PEOPLE or label.get("owner") in OWNER_OTHER):
            item["owner"] = label["owner"]
        if isinstance(label.get("dup"), str) and re.fullmatch(r"[A-Z]", label["dup"]):
            item["dup"] = label["dup"]
        if item:
            cleaned[uid] = item
    return cleaned


def clip_wav(path, start_ms, end_ms, pad_ms=300):
    """16 kHz mono 16-bit WAV bytes of [start-pad, end+pad], read straight from the converted recording."""
    with wave.open(path) as source:
        assert (source.getframerate(), source.getnchannels(), source.getsampwidth()) == (RATE, 1, 2)
        first = max(0, (start_ms - pad_ms) * RATE // 1000)
        last = min(source.getnframes(), (end_ms + pad_ms) * RATE // 1000)
        source.setpos(first)
        frames = source.readframes(max(0, last - first))
    out = io.BytesIO()
    with wave.open(out, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(RATE)
        target.writeframes(frames)
    return out.getvalue()


class Store:
    def __init__(self, session, root):
        self.session, self.root = session, root
        self.folder = os.path.join(root, "evidence", session)
        with open(os.path.join(self.folder, "meeting.json"), encoding="utf-8") as stream:
            self.utterances = json.load(stream)["utterances"]
        self.wav = os.path.join(root, "test-media", f"{session}-16k-mono.wav")
        if not os.path.isfile(self.wav):
            raise FileNotFoundError(self.wav)
        self.path = os.path.join(self.folder, "labels.json")
        self.lock = threading.Lock()
        self.labels = {}
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as stream:
                self.labels = clean_labels(json.load(stream).get("labels"), self.utterances)

    def save(self, raw):
        with self.lock:
            self.labels = clean_labels(raw, self.utterances)
            for name, value in (("labels.json", {"session": self.session, "labels": self.labels}),
                                ("truth.json", derive_truth(self.session, self.utterances, self.labels))):
                temp = os.path.join(self.folder, name + ".tmp")
                with open(temp, "w", encoding="utf-8") as stream:
                    json.dump(value, stream, ensure_ascii=False, indent=1)
                os.replace(temp, os.path.join(self.folder, name))
            return len(self.labels)


PAGE = r"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>会议标注</title>
<style>
:root{color-scheme:light dark;--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff;--brand:#6d5be8}
@media(prefers-color-scheme:dark){:root{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;z-index:2}
header b{font-size:17px} .bar{display:flex;gap:12px;align-items:center;flex-wrap:wrap;color:var(--mut);font-size:13px}
main{max-width:980px;margin:0 auto;padding:8px 16px 80px}
.row{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:8px 0}
.row.done{border-left:4px solid #16a34a}.row.task{border-left:4px solid var(--brand)}
.top{display:flex;gap:10px;align-items:flex-start}.t{color:var(--mut);font-variant-numeric:tabular-nums;min-width:48px;font-size:13px;padding-top:3px}
.txt{flex:1;font-size:16px}button.play{border:1px solid var(--line);background:none;color:var(--fg);border-radius:8px;padding:4px 10px;cursor:pointer}
.ctl{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;align-items:center}
select{font:inherit;padding:4px 6px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg)}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:none;color:var(--fg);padding:5px 10px;cursor:pointer;font:inherit}
.seg button.on{background:var(--brand);color:#fff}.seg button.on.neg{background:#78716c}
label.f{display:flex;gap:6px;align-items:center;color:var(--mut);font-size:13px}
</style>
<header><b>会议标注</b> <span class="bar"><span id="prog"></span>
<label class="f"><input type="checkbox" id="only"> 只看没标完的</label><span id="state">已保存</span></span></header>
<main id="list"></main>
<script>
const PEOPLE=__PEOPLE__, SP_OTHER=__SPOTHER__, OW_OTHER=__OWOTHER__;
let U=[], L={}, timer=null, audio=null;
const $=s=>document.querySelector(s), esc=s=>s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const clock=ms=>{const s=Math.floor(ms/1000);return Math.floor(s/60)+':'+String(s%60).padStart(2,'0')};
const opts=(m,sel,blank)=>`<option value="">${blank}</option>`+PEOPLE.map(p=>`<option ${sel===p?'selected':''}>${p}</option>`).join('')+Object.entries(m).map(([k,v])=>`<option value="${k}" ${sel===k?'selected':''}>${v}</option>`).join('');
function complete(l){return l && l.speaker && l.task && (l.task!=='task' || l.owner)}
function render(){
  const only=$('#only').checked; let done=0, tasks=0;
  $('#list').innerHTML=U.map(u=>{const l=L[u.id]||{}; if(complete(l)) done++; if(l.task==='task') tasks++;
    if(only && complete(l)) return '';
    return `<div class="row ${complete(l)?'done':''} ${l.task==='task'?'task':''}" data-id="${u.id}">
      <div class="top"><span class="t">${clock(u.start_ms)}</span><div class="txt">${esc(u.text)}</div><button class="play">▶ 听</button></div>
      <div class="ctl"><label class="f">谁说的 <select data-k="speaker">${opts(SP_OTHER,l.speaker,'—')}</select></label>
      <span class="seg"><button data-k="task" data-v="task" class="${l.task==='task'?'on':''}">是任务</button><button data-k="task" data-v="not_task" class="neg ${l.task==='not_task'?'on':''}">不是任务</button></span>
      ${l.task==='task'?`<label class="f">负责人 <select data-k="owner">${opts(OW_OTHER,l.owner,'—')}</select></label>`:''}
      <label class="f">同一件事 <select data-k="dup"><option value="">—</option>${'ABCDEFGH'.split('').map(c=>`<option ${l.dup===c?'selected':''}>${c}</option>`).join('')}</select></label></div></div>`}).join('');
  $('#prog').textContent=`已标完 ${done}/${U.length} 句 · 任务 ${tasks} 条`;
}
function save(){ $('#state').textContent='保存中…'; clearTimeout(timer); timer=setTimeout(async()=>{
  const r=await fetch('/api/labels',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(L)});
  $('#state').textContent=r.ok?'已保存':'保存失败'},300)}
$('#list').addEventListener('click',e=>{
  const row=e.target.closest('.row'); if(!row) return; const id=row.dataset.id;
  if(e.target.classList.contains('play')){ if(audio) audio.pause(); audio=new Audio('/clip/'+id+'.wav'); audio.play(); return}
  const k=e.target.dataset.k; if(!k||e.target.tagName!=='BUTTON') return;
  const l=L[id]=L[id]||{}; l[k]=l[k]===e.target.dataset.v?undefined:e.target.dataset.v; if(l.task!=='task') delete l.owner;
  save(); render()});
$('#list').addEventListener('change',e=>{
  const row=e.target.closest('.row'); const k=e.target.dataset.k; if(!row||!k) return;
  const l=L[row.dataset.id]=L[row.dataset.id]||{}; l[k]=e.target.value||undefined; save(); render()});
$('#only').addEventListener('change',render);
fetch('/api/meeting').then(r=>r.json()).then(d=>{U=d.utterances;L=d.labels;document.title='标注 '+d.session.slice(0,8);render()});
</script>"""


def make_handler(store):
    page = (PAGE.replace("__PEOPLE__", json.dumps(PEOPLE, ensure_ascii=False))
            .replace("__SPOTHER__", json.dumps(SPEAKER_OTHER, ensure_ascii=False))
            .replace("__OWOTHER__", json.dumps(OWNER_OTHER, ensure_ascii=False))).encode()
    by_id = {u["id"]: u for u in store.utterances}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, body, kind="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self.send(200, page, "text/html; charset=utf-8")
            if self.path == "/api/meeting":  # blind: text and times only
                rows = [{"id": u["id"], "text": u["text"], "start_ms": u["start_ms"], "end_ms": u["end_ms"]} for u in store.utterances]
                return self.send(200, json.dumps({"session": store.session, "utterances": rows, "labels": store.labels},
                                                 ensure_ascii=False).encode())
            m = re.fullmatch(r"/clip/([A-Za-z0-9_-]{1,40})\.wav", self.path)
            if m and m.group(1) in by_id:
                u = by_id[m.group(1)]
                return self.send(200, clip_wav(store.wav, u["start_ms"], u["end_ms"]), "audio/wav")
            self.send(404, b"{}")

        def do_POST(self):
            if self.path != "/api/labels":
                return self.send(404, b"{}")
            size = int(self.headers.get("Content-Length") or 0)
            if not 0 < size <= 2_000_000:
                return self.send(400, b"{}")
            try:
                saved = store.save(json.loads(self.rfile.read(size)))
            except (json.JSONDecodeError, ValueError):
                return self.send(400, b"{}")
            self.send(200, json.dumps({"saved": saved}).encode())

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session")
    parser.add_argument("--root", default=ROOT)
    parser.add_argument("--port", type=int, default=8796)
    args = parser.parse_args(argv)
    if not SESSION.fullmatch(args.session):
        parser.error("session 应是会议编号（UUID）")
    try:
        store = Store(args.session, args.root)
    except FileNotFoundError as error:
        print(f"缺少 {error}；先运行 archive_to_eval.py", file=sys.stderr)
        return 1
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"标注页：http://127.0.0.1:{args.port}/   （{len(store.utterances)} 句，已有 {len(store.labels)} 句标注；Ctrl+C 退出）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
