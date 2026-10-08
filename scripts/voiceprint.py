#!/usr/bin/env python3
"""Voiceprint prototype: can we tell who is speaking and map them to a Feishu identity?

Run with the voice venv (sherpa-onnx, numpy, soundfile); everything stays on this Mac.

  .venv-voice/bin/python scripts/voiceprint.py analyze        # VAD → embeddings → joint clusters → listening clips
  .venv-voice/bin/python scripts/voiceprint.py label 0=林春源 2=洪玉麟 ...
  .venv-voice/bin/python scripts/voiceprint.py evaluate       # enroll on one recording, identify on the others

Models (models/, gitignored): CAM++ zh-cn speaker embedding (3D-Speaker) and silero VAD.
Outputs go to models/voiceprint/ — voiceprints are sensitive personal data and never leave the Mac.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import sherpa_onnx
import soundfile as sf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.join(ROOT, "models")
OUT = os.path.join(MODELS, "voiceprint")
RATE = 16000


def load(path):
    samples, rate = sf.read(path, dtype="float32")
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if rate != RATE:
        sys.exit(f"{path}: 需要 16 kHz，实际 {rate}")
    return samples


def vad_segments(samples, max_speech_s):
    config = sherpa_onnx.VadModelConfig()
    config.silero_vad.model = os.path.join(MODELS, "silero_vad.onnx")
    config.silero_vad.min_silence_duration = 0.35
    config.silero_vad.min_speech_duration = 0.3
    config.silero_vad.max_speech_duration = max_speech_s
    config.sample_rate = RATE
    vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=120)
    window = config.silero_vad.window_size
    segments = []

    def drain():
        while not vad.empty():
            seg = vad.front
            segments.append((seg.start, seg.start + len(seg.samples)))
            vad.pop()

    for i in range(0, len(samples), window):
        vad.accept_waveform(samples[i:i + window])
        drain()
    vad.flush()
    drain()
    return segments


def turn_segments(samples, max_speech_s):
    """Speaker-turn segments from pyannote segmentation; a discussion has too few pauses for VAD."""
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=os.path.join(MODELS, "sherpa-onnx-pyannote-segmentation-3-0", "model.onnx"))),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=os.path.join(MODELS, "campplus_zh.onnx")),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
    segments = []
    for turn in diarizer.process(samples).sort_by_start_time():
        start, end = int(turn.start * RATE), int(turn.end * RATE)
        step = int(max_speech_s * RATE)
        for s in range(start, end, step):  # long turns → ≤ max_speech_s chunks, each still one speaker
            segments.append((s, min(end, s + step)))
    return segments


def extractor():
    config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
        model=os.path.join(MODELS, "campplus_zh.onnx"), num_threads=4)
    return sherpa_onnx.SpeakerEmbeddingExtractor(config)


def embed(ext, samples):
    stream = ext.create_stream()
    stream.accept_waveform(RATE, samples)
    stream.input_finished()
    v = np.array(ext.compute(stream), dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def recordings():
    files = sorted(glob.glob(os.path.join(ROOT, "test-media", "*-16k-mono.wav")))
    if not files:
        sys.exit("test-media/ 里没有 *-16k-mono.wav")
    return files


def short_name(path):
    # DJI_20261005221824_0235_D-16k-mono.wav → 0235
    parts = os.path.basename(path).split("_")
    return parts[2] if len(parts) > 2 else os.path.basename(path)


def analyze(args):
    os.makedirs(os.path.join(OUT, "clips"), exist_ok=True)
    rows = []  # (recording, start, end, embedding)
    audio = {short_name(p): load(p) for p in recordings()}
    cached = os.path.join(OUT, "segments.npz")
    if args.reuse and os.path.exists(cached):  # re-cluster without re-segmenting (~7 min)
        old = np.load(cached)
        rows = list(zip(old["rec"].tolist(), old["start"].tolist(), old["end"].tolist(), old["emb"]))
    ext = None if rows else extractor()
    for path in ([] if rows else recordings()):
        name = short_name(path)
        samples = load(path)
        audio[name] = samples
        cut = turn_segments if args.segmenter == "turns" else vad_segments
        segs = [s for s in cut(samples, args.max_speech) if (s[1] - s[0]) / RATE >= args.min_dur]
        for start, end in segs:
            rows.append((name, start, end, embed(ext, samples[start:end])))
        speech = sum(e - s for s, e in segs) / RATE / 60
        print(f"{name}: {len(samples) / RATE / 60:5.1f} 分钟录音，{len(segs)} 段 ≥{args.min_dur}s 语音，共 {speech:5.1f} 分钟")

    emb = np.stack([r[3] for r in rows])
    dur = np.array([(r[2] - r[1]) / RATE for r in rows])
    # Cluster on the longer, more reliable segments, then attach the rest to the nearest centroid.
    core = dur >= args.core_dur
    clustering = sherpa_onnx.FastClustering(sherpa_onnx.FastClusteringConfig(num_clusters=args.num_clusters, threshold=args.threshold))
    core_labels = np.array(clustering(emb[core]))
    centroids = []
    for k in sorted(set(core_labels)):
        c = emb[core][core_labels == k].mean(axis=0)
        centroids.append(c / np.linalg.norm(c))
    centroids = np.stack(centroids)
    labels = (emb @ centroids.T).argmax(axis=1)

    # Order clusters by total speaking time; tiny clusters are usually noise / overlaps.
    totals = {k: dur[labels == k].sum() for k in range(len(centroids))}
    order = sorted(totals, key=lambda k: -totals[k])
    remap = {k: i for i, k in enumerate(order)}
    labels = np.array([remap[k] for k in labels])
    centroids = centroids[order]

    names = sorted({r[0] for r in rows})
    print(f"\n聚成 {len(centroids)} 类（阈值 {args.threshold}）。每类说话时长（分钟）：")
    print("类  " + "".join(f"{n:>8}" for n in names) + "    合计  段数")
    for k in range(len(centroids)):
        per = [dur[(labels == k) & np.array([r[0] == n for r in rows])].sum() / 60 for n in names]
        if sum(per) * 60 < 20:
            continue
        print(f"{k:<4}" + "".join(f"{p:8.1f}" for p in per) + f"  {sum(per):6.1f}  {int((labels == k).sum()):4d}")
        # Listening clips: the most typical (closest to centroid) segments of 3–8 s, from different recordings.
        idx = [i for i in np.argsort(-(emb @ centroids[k])) if labels[i] == k and 3 <= dur[i] <= 8]
        picked, seen = [], {}
        for i in idx:
            rec = rows[i][0]
            if seen.get(rec, 0) < 2:
                picked.append(i)
                seen[rec] = seen.get(rec, 0) + 1
            if len(picked) == 3:
                break
        for j, i in enumerate(picked):
            rec, s, e, _ = rows[i]
            sf.write(os.path.join(OUT, "clips", f"cluster{k}_{j + 1}_{rec}_{s // RATE // 60}m{s // RATE % 60:02d}s.wav"),
                     audio[rec][s:e], RATE)

    np.savez(os.path.join(OUT, "segments.npz"), emb=emb, dur=dur, labels=labels, centroids=centroids,
             rec=np.array([r[0] for r in rows]), start=np.array([r[1] for r in rows]),
             end=np.array([r[2] for r in rows]))
    print(f"\n试听片段：{os.path.relpath(os.path.join(OUT, 'clips'), ROOT)}/")


def label(args):
    data = np.load(os.path.join(OUT, "segments.npz"))
    mapping = {}
    for item in args.pairs:
        k, name = item.split("=", 1)
        mapping[int(k)] = name
    with open(os.path.join(OUT, "labels.json"), "w", encoding="utf-8") as f:
        json.dump({str(k): v for k, v in mapping.items()}, f, ensure_ascii=False, indent=2)
    print("已记录：", ", ".join(f"类 {k} = {v}" for k, v in sorted(mapping.items())), f"（共 {len(data['centroids'])} 类）")


def relabel(args):
    """Carry names over to a fresh clustering: each new cluster takes the name of the nearest old one."""
    old = np.load(os.path.join(OUT, args.old))
    old_names = {int(k): v for k, v in json.load(open(os.path.join(OUT, args.old_labels), encoding="utf-8")).items()}
    new = np.load(os.path.join(OUT, "segments.npz"))
    sims = new["centroids"] @ old["centroids"].T
    mapping = {}
    for k, row in enumerate(sims):
        j = int(row.argmax())
        if j in old_names:
            mapping[str(k)] = old_names[j]
            print(f"新类 {k} → {old_names[j]}（相似度 {row[j]:.2f}）")
    if len(set(mapping.values())) != len(mapping):
        print("警告：有两个新类对应到同一个人，请人工核对")
    with open(os.path.join(OUT, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)


def voiceprints(data, truth, enroll_rec, seconds):
    """Each person's print from the segments most typical of them (mixed turns sit far from the centroid)."""
    emb, dur, rec = data["emb"], data["dur"], data["rec"]
    prints = {}
    for p in sorted({t for t in truth if t}):
        mine = np.array([i for i in range(len(dur)) if rec[i] == enroll_rec and truth[i] == p and 1.5 <= dur[i] <= 3.5])
        if not len(mine):
            continue
        centroid = emb[mine].mean(axis=0)
        chosen, total = [], 0.0
        for i in mine[np.argsort(-(emb[mine] @ centroid))]:
            chosen.append(i)
            total += dur[i]
            if total >= seconds:
                break
        v = emb[chosen].mean(axis=0)
        prints[p] = (v / np.linalg.norm(v), total)
    return prints


def identify(emb, prints, accept):
    names = list(prints)
    sims = emb @ np.stack([prints[p][0] for p in names]).T
    return np.array([names[b] if s >= accept else None for b, s in zip(sims.argmax(1), sims.max(1))], dtype=object)


def load_truth():
    data = np.load(os.path.join(OUT, "segments.npz"))
    mapping = {int(k): v for k, v in json.load(open(os.path.join(OUT, "labels.json"), encoding="utf-8")).items()}
    return data, np.array([mapping.get(int(k)) for k in data["labels"]], dtype=object)


def evaluate(args):
    data, truth = load_truth()
    emb, dur, rec = data["emb"], data["dur"], data["rec"]
    enroll_rec = args.enroll or sorted(set(rec))[0]
    prints = voiceprints(data, truth, enroll_rec, args.enroll_seconds)
    print(f"注册：录音 {enroll_rec}，" + "，".join(f"{p} {t:.0f}s" for p, (_, t) in prints.items()))
    answers_path = os.path.join(OUT, "check", "answers.json")
    if args.human:
        answers = json.load(open(answers_path, encoding="utf-8"))
        manifest = json.load(open(os.path.join(OUT, "check", "manifest.json"), encoding="utf-8"))
        idx = np.array([m["segment"] for m in manifest])
        human = np.array([answers.get(m["clip"]) for m in manifest], dtype=object)
        pred = identify(emb[idx], prints, args.accept)
        single = np.array([h in prints for h in human])
        print(f"人工抽查 {len(manifest)} 段：单人 {int(single.sum())}，两人都有 "
              f"{int(sum(h == '混' for h in human))}，听不出 {int(sum(h == '?' for h in human))}，未答 {int(sum(h is None for h in human))}")
        decided = single & (pred != None)  # noqa: E711
        print(f"单人片段：判定率 {decided.sum() / max(1, single.sum()):.0%}，判定后准确率 "
              f"{(pred[decided] == human[decided]).mean() if decided.sum() else float('nan'):.1%}（阈值 {args.accept}）")
        auto = truth[idx]
        print(f"自动聚类标签与人工一致：{(auto[single] == human[single]).mean():.1%}")
        for i in np.where(decided & (pred != human))[0]:
            print(f"  认错：{manifest[i]['clip']} 人工={human[i]} 识别={pred[i]}")
        return
    test = (rec != enroll_rec) & np.array([t is not None for t in truth])
    pred = identify(emb[test], prints, args.accept)
    t, d = truth[test], dur[test]
    print(f"测试：其余录音 {int(test.sum())} 段，接受阈值 {args.accept}（标准答案来自自动聚类）")
    print("时长       段数  判定率  判定后准确率")
    for lo, hi in [(1, 2), (2, 3), (3, 99)]:
        m = (d >= lo) & (d < hi)
        if m.sum() == 0:
            continue
        decided = m & (pred != None)  # noqa: E711
        acc = (pred[decided] == t[decided]).mean() if decided.sum() else float("nan")
        print(f"{lo:>2}–{hi if hi < 99 else '∞':<3}秒 {int(m.sum()):6d}  {decided.sum() / m.sum():6.0%}  {acc:8.1%}")
    print("\n按人：")
    for p in prints:
        m = t == p
        hit = (pred[m] == p).sum()
        claimed = (pred == p).sum()
        print(f"  {p}: 精确 {hit / claimed if claimed else float('nan'):.1%}，召回 {hit / m.sum():.1%}（{int(m.sum())} 段）")


def enroll(args):
    """Writes the voiceprints the local backend uses (models/voiceprint/enrolled.json)."""
    data, truth = load_truth()
    rec = args.recording or sorted(set(data["rec"]))[0]
    prints = voiceprints(data, truth, rec, args.seconds)
    out = {"model": "3dspeaker_speech_campplus_sv_zh-cn_16k-common", "source": f"recording {rec}",
           "people": {p: {"seconds": round(t, 1), "vector": [round(float(x), 6) for x in v]}
                      for p, (v, t) in prints.items()}}
    path = os.path.join(OUT, "enrolled.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"已注册 {len(prints)} 人 → {os.path.relpath(path, ROOT)}：" + "，".join(f"{p} {t:.0f}s" for p, (_, t) in prints.items()))


CHECK_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>声纹抽查</title>
<style>
:root{--bg:#f7f6fb;--card:#fff;--text:#1b1530;--muted:#6b6880;--brand:#6e56f8;--line:#e7e4f0}
@media (prefers-color-scheme:dark){:root{--bg:#0f0d17;--card:#1a1726;--text:#ecebf6;--muted:#9c98b0;--brand:#8b76ff;--line:#2c2840}}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif}
main{max-width:760px;margin:0 auto;padding:20px 16px 80px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--muted);margin:0 0 16px;font-size:13px}
.item{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px;margin:10px 0}
.head{display:flex;gap:10px;align-items:center;margin-bottom:8px}.no{font-weight:700;color:var(--brand);min-width:28px}
audio{width:100%;height:36px}.text{margin:6px 0 0;font-size:15px}.text.none{color:var(--muted)}.opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
label{border:1px solid var(--line);border-radius:10px;padding:5px 10px;cursor:pointer;font-size:14px}
label:has(input:checked){border-color:var(--brand);background:color-mix(in srgb,var(--brand) 14%,transparent)}
input{display:none}.bar{position:fixed;left:0;right:0;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;justify-content:center}
button{background:var(--brand);color:#fff;border:0;border-radius:10px;padding:9px 18px;font:inherit;font-weight:600;cursor:pointer}
#status{color:var(--muted);font-size:13px}
</style><main><h1>声纹抽查</h1>
<p class="sub">每段 2–4 秒，来自 0236 / 0238 两场录音。文字是语音识别结果，可能有错字，也可能混进旁人的话。听一下是谁在说话；两个人都有选「两人都有」，听不清选「听不出」。答案只保存在这台 Mac。</p>
<div id="list"></div></main>
<div class="bar"><span id="status"></span><button id="save">保存答案</button></div>
<script>
const CHOICES=__CHOICES__;const CLIPS=__CLIPS__;let answers={};let texts={};
const list=document.getElementById('list');
const get=f=>fetch(f).then(r=>r.ok?r.json():{}).catch(()=>({}));
Promise.all([get('answers.json'),get('transcripts.json')]).then(([a,t])=>{answers=a||{};texts=t||{};render();});
function render(){list.innerHTML='';CLIPS.forEach((c,i)=>{const d=document.createElement('div');d.className='item';
d.innerHTML=`<div class="head"><span class="no">${i+1}</span><audio controls preload="none" src="${c}"></audio></div>`+
(texts[c]?`<p class="text">${texts[c].replace(/[&<>]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]))}</p>`:'<p class="text none">（没识别出文字）</p>')+'<div class="opts">'+
CHOICES.map(([v,t])=>`<label><input type="radio" name="${c}" value="${v}" ${answers[c]===v?'checked':''}>${t}</label>`).join('')+'</div>';
d.addEventListener('change',e=>{answers[c]=e.target.value;count();});list.appendChild(d);});count();}
function count(){document.getElementById('status').textContent=`已答 ${Object.keys(answers).length} / ${CLIPS.length}`;}
document.getElementById('save').onclick=()=>fetch('save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(answers)})
.then(r=>{document.getElementById('status').textContent=r.ok?'已保存，可以回到对话里说一声':'保存失败';});
</script></html>"""


def spotcheck(args):
    """Export N short clips from the test recordings, evenly across predicted speakers, and serve a labelling page."""
    import random
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    data, truth = load_truth()
    rec, dur, start, end = data["rec"], data["dur"], data["start"], data["end"]
    folder = os.path.join(OUT, "check")
    manifest_path = os.path.join(folder, "manifest.json")
    if not os.path.exists(manifest_path) or args.fresh:
        os.makedirs(folder, exist_ok=True)
        for f in glob.glob(os.path.join(folder, "*.wav")):
            os.remove(f)
        rng = random.Random(args.seed)
        enroll_rec = sorted(set(rec))[0]
        people = sorted({t for t in truth if t})
        picked = []
        for p in people:
            pool = [i for i in range(len(dur)) if rec[i] != enroll_rec and truth[i] == p and 2.0 <= dur[i] <= 4.0]
            picked += rng.sample(pool, min(len(pool), args.count // len(people)))
        rng.shuffle(picked)
        audio = {short_name(p): load(p) for p in recordings()}
        manifest = []
        for n, i in enumerate(picked, 1):
            clip = f"{n:02d}.wav"
            sf.write(os.path.join(folder, clip), audio[rec[i]][int(start[i]):int(end[i])], RATE)
            manifest.append({"clip": clip, "segment": int(i), "recording": str(rec[i]),
                             "start_s": round(int(start[i]) / RATE, 1)})
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    people = sorted({t for t in truth if t})
    choices = [[p, p] for p in people] + [["混", "两人都有"], ["?", "听不出"]]
    page = CHECK_PAGE.replace("__CHOICES__", json.dumps(choices, ensure_ascii=False)) \
                     .replace("__CLIPS__", json.dumps([m["clip"] for m in manifest]))
    open(os.path.join(folder, "index.html"), "w", encoding="utf-8").write(page)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=folder, **kw)

        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/save":
                return self.send_error(404)
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            json.dump(body, open(os.path.join(folder, "answers.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            self.send_response(204)
            self.end_headers()
            print(f"已保存 {len(body)} 条答案", flush=True)

    print(f"抽查页面：http://127.0.0.1:{args.port}/  （{len(manifest)} 段，Ctrl+C 结束）", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--min-dur", type=float, default=1.0)
    a.add_argument("--core-dur", type=float, default=2.5)
    a.add_argument("--max-speech", type=float, default=3.0, help="每段最长秒数；讨论中换人很快，太长会混进两个人")
    a.add_argument("--threshold", type=float, default=0.8)
    a.add_argument("--segmenter", choices=["turns", "vad"], default="turns")
    a.add_argument("--reuse", action="store_true", help="复用上次的分段和声纹，只重新聚类")
    a.add_argument("--num-clusters", type=int, default=-1, help="已知人数时直接指定（-1 = 按阈值自动）")
    l = sub.add_parser("label")
    l.add_argument("pairs", nargs="+", help="类号=姓名")
    r = sub.add_parser("relabel", help="把上一次聚类的人名对应到新的聚类")
    r.add_argument("--old", default="segments-8s.npz")
    r.add_argument("--old-labels", default="labels-8s.json")
    e = sub.add_parser("evaluate")
    e.add_argument("--enroll", help="用哪段录音注册（默认第一段）")
    e.add_argument("--enroll-seconds", type=float, default=30.0)
    e.add_argument("--accept", type=float, default=0.5, help="相似度低于此值判为未知")
    e.add_argument("--human", action="store_true", help="用人工抽查的答案评估")
    n = sub.add_parser("enroll", help="导出注册声纹给本地后端用")
    n.add_argument("--recording", help="用哪段录音（默认第一段）")
    n.add_argument("--seconds", type=float, default=30.0)
    c = sub.add_parser("spotcheck", help="导出抽查片段并打开本地标注页面")
    c.add_argument("--count", type=int, default=40)
    c.add_argument("--seed", type=int, default=7)
    c.add_argument("--port", type=int, default=8791)
    c.add_argument("--fresh", action="store_true", help="重新抽样（会覆盖旧片段）")
    c.add_argument("--split", action="store_true", help="独立句内切点/说话人/承诺人标注，不使用自动聚类作真值")
    s = sub.add_parser("split-prepare", help="冻结分层抽样集（两场完整 ASR 结果，开发/验收分离）")
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--count", type=int, default=55)
    v = sub.add_parser("split-eval", help="开发集调参、独立验收三档拆分和负责人；未标注不生成通过配置")
    v.add_argument("--tune", action="store_true", help="仅用开发集扫描并冻结 short 阈值")
    v.add_argument("--accept", type=float, default=.5)
    v.add_argument("--margin", type=float, default=.05)
    v.add_argument("--no-jev", action="store_true")
    v.add_argument("--jev-env", default=".jev.env")
    v.add_argument("--jev-model", default="jev-latest")
    for parser in (c, s, v):
        parser.add_argument("--dataset", default="dji", help="独立题集ID；现场链路应使用新ID")
        parser.add_argument("--dataset-root", help="独立题集根目录（默认 models/voiceprint）")
        parser.add_argument("--registry-snapshot", help="验收使用的线上实际声纹快照；不拉取音频")
    for parser in (c, s):
        parser.add_argument("--dev-recording", action="append", default=[], help="开发场录音文件名，可重复")
        parser.add_argument("--holdout-recording", action="append", default=[], help="独立保留场录音文件名，可重复")
        parser.add_argument("--recordings-root", help="本机 WAV 文件目录")
        parser.add_argument("--source", default="DJI", help="题集采集链路说明")
    v.add_argument("--version", help="开发阶段冻结的唯一评估版本")
    v.add_argument("--holdout-case-count", type=int, help="预先固定的保留句数量；默认现有manifest的完整保留集")
    v.add_argument("--holdout-recording-count", type=int, help="预先固定的独立保留场次数量")
    v.add_argument("--holdout-cutoff", help="预先固定的验收截止日期（UTC ISO格式）")
    v.add_argument("--holdout-self-count", type=int, default=0, help="预先固定的本人承诺样本目标")
    v.add_argument("--holdout-named-count", type=int, default=0, help="预先固定的点名样本目标")
    v.add_argument("--a-threshold", type=float, default=.98)
    v.add_argument("--b-threshold", type=float, default=.5)
    args = p.parse_args()
    if args.cmd in ("split-prepare", "split-eval") or (args.cmd == "spotcheck" and args.split):
        import voiceprint_split
        return {"split-prepare": voiceprint_split.prepare, "split-eval": voiceprint_split.evaluate,
                "spotcheck": voiceprint_split.serve}[args.cmd](args)
    {"analyze": analyze, "label": label, "relabel": relabel, "evaluate": evaluate, "spotcheck": spotcheck, "enroll": enroll}[args.cmd](args)


if __name__ == "__main__":
    main()
