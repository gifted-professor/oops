#!/usr/bin/env python3
"""Independent, blind split annotation and frozen development/holdout evaluation.

Called by voiceprint.py split-prepare / spotcheck --split / split-eval. Automatic
clustering is used only to stratify sampling, never as ground truth. No acceptance
artifact is emitted until the holdout has been reviewed by a human.
"""
import hashlib
import json
from pathlib import Path
import random
import time
import unicodedata
import wave

from local_backend import (ROOT, TEAM, Speakers, commitment_evidence, align_words, resolve_owner,
                           load_jev, voiceprint_decision, split_fingerprints)

OUT = Path(ROOT) / "models/voiceprint"
FOLDER = OUT / "split-check"
TRUTH = OUT / "split-truth.json"
FROZEN = OUT / "split-config.json"
VALIDATION = OUT / "split-validation.json"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def corpus():
    found = {}
    for summary in sorted((Path(ROOT) / "evidence").glob("asr-words*/**/summary.json")):
        report = read(summary)
        if not report.get("complete") or report.get("transport_errors"):
            continue
        recording = report["recording"]
        if "_0236_" not in recording and "_0238_" not in recording:
            continue
        source = summary.parent / "finals.json"
        if recording not in found or source.stat().st_mtime > found[recording].stat().st_mtime:
            found[recording] = source
    if len(found) != 2:
        raise ValueError("需要两场完整、无错误的 Phase 0 ASR 结果；先完成 asr_words_probe.py")
    rows = []
    for recording, source in sorted(found.items()):
        utterances = read(source)
        for i, u in enumerate(utterances):
            start, end = u.get("start_time", 0), u.get("end_time", 0)
            if end <= start or end - start > 30000:
                continue
            words = [{"text": w.get("text", w.get("word", "")), "start_ms": w.get("start_time", 0),
                      "end_ms": w.get("end_time", 0)} for w in u.get("words", [])]
            units, approx, coverage = align_words(u["text"], words, start, end)
            boundaries, offset = [], 0
            for unit in units[:-1]:
                offset += len(unit["text"])
                boundaries.append({"char": offset, "time_ms": unit["end_ms"]})
            rows.append({"id": recording.split("_")[2] + f"-u{i + 1}", "recording": recording,
                         "partition": "dev" if "_0236_" in recording else "holdout",
                         "start_ms": start, "end_ms": end, "text": u["text"], "words": words,
                         "boundaries": boundaries, "approx": approx, "coverage": coverage,
                         "context": [{"text": old["text"], "speaker": None} for old in utterances[max(0, i - 4):i]]})
    return rows


def prepare(args):
    if (FOLDER / "manifest.json").exists():
        print("复用已有冻结标注集（不覆盖人工答案）：", FOLDER / "manifest.json")
        return
    pool = corpus()
    rng = random.Random(args.seed)
    # Sampling hints are model output only. Gold labels are entered independently in the page.
    turns = read(OUT / "split-turn-hints.json", {})
    if not turns and (OUT / "segments.npz").exists():
        import numpy as np
        data = np.load(OUT / "segments.npz")
        for rec in ("0236", "0238"):
            indices = [i for i, r in enumerate(data["rec"]) if str(r) == rec]
            ordered = sorted(indices, key=lambda i: int(data["start"][i]))
            times = []
            for a, b in zip(ordered, ordered[1:]):
                if data["labels"][a] != data["labels"][b]:
                    times.append(int(data["start"][b]) / 16)
            turns[rec] = times
    chosen, used = [], set()
    categories = [("self", 15, lambda u: commitment_evidence(u["text"])[0] == "self"),
                  ("short_or_turn", 10, lambda u: any(u["start_ms"] - 1500 <= t <= u["end_ms"] + 1500
                       for t in turns.get(u["recording"].split("_")[2], []))),
                  ("long", 15, lambda u: u["end_ms"] - u["start_ms"] > 6000),
                  ("possible_overlap", 5, lambda u: sum(u["start_ms"] <= t <= u["end_ms"]
                       for t in turns.get(u["recording"].split("_")[2], [])) >= 3),
                  ("random", 10, lambda u: True)]
    for category, count, predicate in categories:
        eligible = [u for u in pool if u["id"] not in used and predicate(u)]
        rng.shuffle(eligible)
        picked = []
        while eligible and len(picked) < count:
            candidate = min(eligible, key=lambda u: sum(c["partition"] == u["partition"] for c in chosen + picked))
            picked.append(candidate)
            eligible.remove(candidate)
        if len(picked) < count:
            print(f"{category}: 实际只有 {len(picked)}/{count} 个候选，需人工核实/补样，不伪造分层数量")
        for u in picked:
            chosen.append(dict(u, category=category))
            used.add(u["id"])
    # Fill the total with blind random cases; keep the true strata counts in the manifest.
    rest = [u for u in pool if u["id"] not in used]
    rng.shuffle(rest)
    chosen.extend(dict(u, category="random_fill") for u in rest[:max(0, 55 - len(chosen))])
    FOLDER.mkdir(parents=True, exist_ok=True)
    for u in chosen:
        clip = u["id"] + ".wav"
        with wave.open(str(Path(ROOT) / "test-media" / u["recording"])) as source:
            source.setpos(u["start_ms"] * 16)
            pcm = source.readframes((u["end_ms"] - u["start_ms"]) * 16)
            # The provider can include final transport silence beyond the original WAV.
            pcm = pcm.ljust((u["end_ms"] - u["start_ms"]) * 32, b"\0")
        with wave.open(str(FOLDER / clip), "wb") as dest:
            dest.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            dest.writeframes(pcm)
        u["clip"] = clip
    manifest = {"schema": 1, "seed": args.seed, "cases": sorted(chosen, key=lambda u: (u["recording"], u["start_ms"])),
                "partition_rule": "0236 development; 0238 independent holdout"}
    manifest["hash"] = digest(manifest)
    write(FOLDER / "manifest.json", manifest)
    print(json.dumps({"cases": len(chosen), "dev": sum(u["partition"] == "dev" for u in chosen),
                      "holdout": sum(u["partition"] == "holdout" for u in chosen), "manifest": str(FOLDER / "manifest.json")}, ensure_ascii=False))


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Oops 独立说话人标注</title>
<style>body{font:16px/1.6 system-ui;margin:0;background:#f4f4fa;color:#191526}main{max-width:960px;margin:auto;padding:20px 20px 90px}.case{background:white;padding:18px;border-radius:12px;margin:18px 0}.text button{font:inherit;padding:2px;border:0;background:transparent;color:#191526}.text button.cut{border-right:3px solid #7255ee}.text button:disabled{opacity:1;color:#666}.segment{padding:8px;border-left:3px solid #7255ee;margin:8px 0}select,button{font:inherit}audio{width:100%}.bar{position:fixed;bottom:0;background:white;padding:15px;width:100%;box-sizing:border-box}small{color:#666}button{cursor:pointer}label{margin-right:12px}</style>
<main><h1>独立说话人标注</h1><p>先听整句，再点文字的可用词边界切段。每段勾选实际说话人；重叠时可选两人。看不到模型的人名预测。确认本句是否含自己承诺，再选择承诺人。听不清可标记“听不出”。</p><small>开发集与验收集已按录音分开。答案保存在本机；完成标注后再运行 split-eval。</small><div id="list"></div></main><div class="bar"><button id="save">保存标注</button> <span id="status"></span></div>
<script>
let manifest,truth={};const team=__TEAM__;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
Promise.all([fetch('manifest.json').then(r=>r.json()),fetch('truth').then(r=>r.json())]).then(([m,t])=>{manifest=m;truth=t?.answers||{};render();});
function answer(u){return truth[u.id]||(truth[u.id]={cuts:[],segments:[{speakers:[]}],self_present:null,self_owner:null,reviewed:false});}
function render(){const list=document.getElementById('list');list.innerHTML='';manifest.cases.forEach((u,n)=>{
 const a=answer(u),d=document.createElement('section');d.className='case';const allowed=new Set(u.boundaries.map(b=>b.char));
 d.innerHTML=`<h3>${n+1}. ${esc(u.id)} · ${u.partition==='dev'?'开发集':'验收集'}</h3><audio controls preload="none" src="${esc(u.clip)}"></audio><p class="text">${Array.from(u.text).map((c,i)=>`<button data-cut="${i+1}" class="${a.cuts.includes(i+1)?'cut':''}" ${allowed.has(i+1)?'':'disabled'}>${esc(c)}</button>`).join('')}</p>`;
 const chars=Array.from(u.text),points=[0,...a.cuts,chars.length];points.slice(0,-1).forEach((start,i)=>{const seg=a.segments[i]||(a.segments[i]={speakers:[]});const box=document.createElement('div');box.className='segment';box.innerHTML=`<div>${esc(chars.slice(start,points[i+1]).join(''))}</div>`+[...team,'未注册人员','听不出'].map(name=>`<label><input type="checkbox" data-segment="${i}" value="${esc(name)}" ${seg.speakers.includes(name)?'checked':''}>${esc(name)}</label>`).join('');d.appendChild(box);});
 const extra=document.createElement('div');extra.innerHTML=`<p>本句是否含自己承诺？ <select data-self><option value="">请选择</option><option value="yes" ${a.self_present===true?'selected':''}>有</option><option value="no" ${a.self_present===false?'selected':''}>没有</option></select> 承诺人 <select data-owner><option value="">请选择</option>${team.map(name=>`<option ${a.self_owner===name?'selected':''}>${esc(name)}</option>`).join('')}</select></p><label><input type="checkbox" data-reviewed ${a.reviewed?'checked':''}>我已听过并核实本句</label>`;d.appendChild(extra);
 d.onclick=e=>{const cut=e.target.closest('[data-cut]');if(!cut||cut.disabled)return;const at=Number(cut.dataset.cut);a.cuts=a.cuts.includes(at)?a.cuts.filter(c=>c!==at):[...a.cuts,at].sort((x,y)=>x-y);a.segments=Array.from({length:a.cuts.length+1},()=>({speakers:[]}));a.reviewed=false;render();};
 d.onchange=e=>{if(e.target.dataset.segment!==undefined){const seg=a.segments[Number(e.target.dataset.segment)];seg.speakers=e.target.checked?[...new Set([...seg.speakers,e.target.value])]:seg.speakers.filter(v=>v!==e.target.value);}else if(e.target.hasAttribute('data-self'))a.self_present=e.target.value===''?null:e.target.value==='yes';else if(e.target.hasAttribute('data-owner'))a.self_owner=e.target.value||null;else if(e.target.hasAttribute('data-reviewed'))a.reviewed=e.target.checked;count();};list.appendChild(d);
 });count();}
function count(){document.getElementById('status').textContent=`已核实 ${Object.values(truth).filter(a=>a.reviewed).length}/${manifest.cases.length} 句`;}
document.getElementById('save').onclick=()=>fetch('save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_hash:manifest.hash,answers:truth})}).then(async r=>{document.getElementById('status').textContent=r.ok?'已保存，继续标注或回到 Codex':await r.text();});
</script></html>'''


def validated_truth(body, manifest):
    if body.get("manifest_hash") != manifest["hash"] or not isinstance(body.get("answers"), dict):
        raise ValueError("标注集版本不匹配")
    cases = {u["id"]: u for u in manifest["cases"]}
    for key, a in body["answers"].items():
        u = cases.get(key)
        if u is None or not isinstance(a, dict):
            raise ValueError("无效句子编号")
        allowed = {b["char"] for b in u["boundaries"]}
        cuts = a.get("cuts", [])
        if not isinstance(cuts, list) or cuts != sorted(set(cuts)) or any(c not in allowed for c in cuts):
            raise ValueError("切点必须位于可对齐词边界")
        segments = a.get("segments", [])
        if not isinstance(segments, list) or len(segments) != len(cuts) + 1:
            raise ValueError("分段数量不匹配")
        for s in segments:
            if not isinstance(s.get("speakers"), list) or any(n not in TEAM + ["未注册人员", "听不出"] for n in s["speakers"]):
                raise ValueError("无效说话人")
            if len(set(s["speakers"])) != len(s["speakers"]) or ("听不出" in s["speakers"] and len(s["speakers"]) != 1):
                raise ValueError("听不出不能同时标为已知说话人，名单不能重复")
        if a.get("reviewed") and (any(not s["speakers"] for s in segments) or a.get("self_present") not in (True, False)
                                  or (a["self_present"] and a.get("self_owner") not in TEAM)):
            raise ValueError(f"{key} 已核实但缺少说话人或承诺人标注")
    return body


def serve(args):
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    if not (FOLDER / "manifest.json").exists():
        prepare(args)
    manifest = read(FOLDER / "manifest.json")
    (FOLDER / "index.html").write_text(PAGE.replace("__TEAM__", json.dumps(TEAM, ensure_ascii=False)))

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(FOLDER), **kw)

        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path == "/truth":
                data = json.dumps(read(TRUTH, {"manifest_hash": manifest["hash"], "answers": {}}), ensure_ascii=False).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            else:
                super().do_GET()

        def do_POST(self):
            if self.path != "/save":
                return self.send_error(404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000:
                    raise ValueError("标注体过大或为空")
                body = validated_truth(json.loads(self.rfile.read(size)), manifest)
                write(TRUTH, body)
                self.send_response(204); self.end_headers()
                print("已保存人工核实句数：", sum(a.get("reviewed", False) for a in body["answers"].values()), flush=True)
            except (ValueError, TypeError, KeyError) as exc:
                data = str(exc).encode("utf-8")
                self.send_response(400); self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    print(f"独立标注页面：http://127.0.0.1:{args.port}/ （{len(manifest['cases'])} 句；不显示模型预测）", flush=True)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


def gold_segments(u, answer):
    points = [0] + answer["cuts"] + [len(u["text"])]
    times = {b["char"]: b["time_ms"] for b in u["boundaries"]} | {0: u["start_ms"], len(u["text"]): u["end_ms"]}
    return [{"text": u["text"][a:b], "char_start": a, "char_end": b, "start_ms": times[a], "end_ms": times[b],
             "speakers": s["speakers"]} for a, b, s in zip(points, points[1:], answer["segments"])]


def metrics(cases, answers, predictions, jev=None):
    total = correct = claimed = wrong = overlap_total = overlap_correct = 0
    boundaries = hits = false_points = short_total = short_correct = short_wrong = 0
    self_total = self_claimed = self_correct = 0
    for u in cases:
        a, pred = answers[u["id"]], predictions[u["id"]]
        gold = gold_segments(u, a)
        names = [s.get("speaker") for s in pred for _ in s["text"]]
        if len(names) != len(u["text"]):
            raise ValueError("预测未保留完整原文")
        for s in gold:
            if "听不出" in s["speakers"]:
                continue
            indices = [i for i in range(s["char_start"], s["char_end"])
                       if not u["text"][i].isspace() and not unicodedata.category(u["text"][i]).startswith("P")]
            if len(s["speakers"]) > 1:
                overlap_total += len(indices)
                overlap_correct += sum(names[i] in s["speakers"] for i in indices)
                continue
            target = s["speakers"][0]
            total += len(indices)
            correct += sum(names[i] == target for i in indices)
            claimed += sum(names[i] is not None for i in indices)
            wrong += sum(names[i] is not None and names[i] != target for i in indices)
            if s["end_ms"] - s["start_ms"] < 1500 and indices:
                short_total += 1
                short_correct += sum(names[i] == target for i in indices) > len(indices) / 2
                counts = {n: sum(names[i] == n for i in indices) for n in set(names[i] for i in indices) if n is not None and n != target}
                short_wrong += bool(counts and max(counts.values()) > len(indices) / 2)
        gt = [b["start_ms"] for a_, b in zip(gold, gold[1:]) if len(a_["speakers"]) == len(b["speakers"]) == 1
              and "听不出" not in a_["speakers"] + b["speakers"] and a_["speakers"] != b["speakers"]]
        pp = [b["start_ms"] for a_, b in zip(pred, pred[1:]) if a_["speaker"] != b["speaker"]]
        # Overlap and inaudible gold spans are excluded from every main metric.
        excluded = [s for s in gold if len(s["speakers"]) > 1 or "听不出" in s["speakers"]]
        pp = [at for at in pp if not any(s["start_ms"] <= at <= s["end_ms"] for s in excluded)]
        available = list(pp)
        boundaries += len(gt)
        for at in gt:
            near = min(available, key=lambda p: abs(p - at)) if available else None
            if near is not None and abs(near - at) <= 500:
                hits += 1; available.remove(near)
        false_points += len(available)
        if a.get("self_present"):
            self_total += 1
            owner = resolve_owner({"utterances": [{"text": u["text"], "segments": pred}], "context": u["context"]}, jev, True)
            if owner is not None:
                self_claimed += 1
                self_correct += owner == a["self_owner"]
    return {"char_accuracy": correct / max(1, total), "wrong_claim_rate": wrong / max(1, claimed),
            "unknown_rate": (total - claimed) / max(1, total), "chars": total, "claimed_chars": claimed,
            "change_recall": hits / max(1, boundaries), "change_count": boundaries, "change_hits": hits,
            "false_change_points": false_points, "short_recall": short_correct / max(1, short_total),
            "short_wrong_rate": short_wrong / max(1, short_total), "short_count": short_total,
            "self_coverage": self_claimed / max(1, self_total), "self_accuracy": self_correct / max(1, self_claimed),
            "self_count": self_total, "self_claimed": self_claimed, "self_wrong": self_claimed - self_correct,
            "overlap_chars": overlap_total, "overlap_any_speaker_accuracy": overlap_correct / max(1, overlap_total)}


def gates(final, baseline):
    owner_passed = (final["self_count"] > 0 and final["self_coverage"] >= .6 and final["self_claimed"] > 0
                    and (final["self_wrong"] == 0 if final["self_claimed"] <= 20 else final["self_accuracy"] >= .95))
    split_passed = (final["chars"] > 0 and final["char_accuracy"] >= baseline["char_accuracy"] + .1
                    and final["wrong_claim_rate"] <= min(.05, baseline["wrong_claim_rate"])
                    and final["change_count"] > 0 and final["change_recall"] >= .7
                    and final["short_count"] > 0 and final["short_recall"] >= .5 and final["short_wrong_rate"] <= .1)
    return split_passed, owner_passed


def evaluate(args):
    manifest = read(FOLDER / "manifest.json")
    truth = read(TRUTH)
    if not manifest or not truth or truth.get("manifest_hash") != manifest["hash"]:
        print("待人工标注：voiceprint.py spotcheck --split；未生成通过验收的配置")
        return
    validated_truth(truth, manifest)
    answers = truth["answers"]
    partitions = {part: [u for u in manifest["cases"] if u["partition"] == part] for part in ("dev", "holdout")}
    required = partitions["dev"] if args.tune else manifest["cases"]
    missing = [u["id"] for u in required if not answers.get(u["id"], {}).get("reviewed")]
    if missing:
        print(json.dumps({"status": "pending_human", "unreviewed_count": len(missing), "ids": missing}, ensure_ascii=False))
        return
    speaker = Speakers(args.accept, args.margin)
    if not speaker.ready:
        raise ValueError("声纹模型未加载：" + str(speaker.error))
    # Model calls are cached across the parameter scan; the labels never enter extraction.
    original_scores, cache = speaker.scores, {}
    def cached_scores(pcm):
        key = hashlib.sha256(pcm).hexdigest()
        if key not in cache:
            cache[key] = original_scores(pcm)
        return cache[key]
    speaker.scores = cached_scores
    jev = None if args.no_jev else load_jev(args)
    owner_cache = {}
    if jev:
        original_choice = jev.choice
        def cached_choice(state, instructions, criteria, timeout=6.0):
            key = digest([state, instructions, criteria])
            if key not in owner_cache:
                owner_cache[key] = original_choice(state, instructions, criteria, timeout)
            return owner_cache[key]
        jev.choice = cached_choice

    def predict(cases, config, use_jev=False, baseline=False):
        predictions = {}
        for i, u in enumerate(cases, 1):
            with wave.open(str(FOLDER / u["clip"])) as audio:
                pcm = audio.readframes(audio.getnframes())
            if baseline:
                scores = speaker.scores(pcm)
                name, score = voiceprint_decision(scores, speaker.names, u["end_ms"] - u["start_ms"],
                    args.accept, args.margin, args.accept, args.margin)
                segments = [{"start_ms": u["start_ms"], "end_ms": u["end_ms"], "text": u["text"], "speaker": name,
                             "score": score, "source": "voiceprint" if name else None, "approx": u["approx"]}]
            else:
                segments, _ = speaker.split(pcm, u["text"], u["words"], u["start_ms"], u["context"], jev if use_jev else None, config)
            predictions[u["id"]] = segments
            if i % 10 == 0:
                print(json.dumps({"progress": i, "total": len(cases), "mode": "baseline" if baseline else "jev" if use_jev else "voiceprint"}), flush=True)
        return predictions

    config = {"accept": args.accept, "margin": args.margin, "short_accept": args.accept + .1,
              "short_margin": args.margin + .05, "switch_penalty": .8, "use_jev": False}
    if args.tune:
        if FROZEN.exists():
            raise ValueError("已有冻结参数；不覆盖或在看到验收数字后重调同一验收集")
        dev = partitions["dev"]
        results = []
        for da in (.05, .1, .15):
            for dm in (.025, .05, .075):
                candidate = config | {"short_accept": args.accept + da, "short_margin": args.margin + dm}
                predictions = predict(dev, candidate)
                numbers = metrics(dev, answers, predictions)
                results.append({"config": candidate, "metrics": numbers})
        eligible = [r for r in results if r["metrics"]["short_count"] > 0 and r["metrics"]["short_wrong_rate"] <= .1]
        selected = max(eligible or results, key=lambda r: (r["metrics"]["short_recall"], r["metrics"]["char_accuracy"]))
        write(FROZEN, {"schema": 1, "manifest_hash": manifest["hash"], "dev_ids": [u["id"] for u in dev],
                       "fingerprints": split_fingerprints(),
                       "dev_truth_hash": digest({u["id"]: answers[u["id"]] for u in dev}),
                       "config": selected["config"], "scan": results, "frozen_at": time.time()})
        print("开发集参数已冻结：", FROZEN, flush=True)
        return
    frozen = read(FROZEN)
    if not frozen or frozen["manifest_hash"] != manifest["hash"]:
        raise ValueError("先运行 split-eval --tune，用开发集冻结参数；验收不负责调参")
    if frozen["dev_truth_hash"] != digest({u["id"]: answers[u["id"]] for u in partitions["dev"]}):
        raise ValueError("开发集标注已变化，冻结参数不再对应当前标注；需要新的评估版本")
    if frozen.get("fingerprints") != split_fingerprints():
        raise ValueError("调参后代码、模型或注册声纹已变化，需要新的评估版本")
    config = frozen["config"]
    holdout = partitions["holdout"]
    baseline = metrics(holdout, answers, predict(holdout, config, baseline=True), jev)
    pure = metrics(holdout, answers, predict(holdout, config), jev)
    with_jev = metrics(holdout, answers, predict(holdout, config, use_jev=True), jev) if jev else None
    keep_jev = False
    if with_jev:
        jsplit, _ = gates(with_jev, baseline)
        keep_jev = jsplit and pure["unknown_rate"] - with_jev["unknown_rate"] >= .05 \
            and with_jev["wrong_claim_rate"] <= pure["wrong_claim_rate"] + .01
    final = with_jev if keep_jev else pure
    split_passed, owner_passed = gates(final, baseline)
    self_cases = sum(answers[u["id"]].get("self_present") is True for u in manifest["cases"])
    owner_passed = owner_passed and self_cases >= 15
    config = config | {"use_jev": keep_jev}
    report = {"schema": 1, "manifest_hash": manifest["hash"], "truth_hash": digest(truth),
              "fingerprints": split_fingerprints(),
              "holdout_count": len(holdout), "holdout_ids": [u["id"] for u in holdout],
              "split_passed": split_passed, "self_owner_passed": split_passed and owner_passed,
              "labeled_self_count": self_cases, "required_self_count": 15,
              "config": config, "baseline": baseline, "voiceprint": pure, "voiceprint_jev": with_jev,
              "jev_evaluated": bool(jev), "jev_retained": keep_jev,
              "limitations": "Demonstration sample, not a population-level 95% accuracy guarantee."}
    write(VALIDATION, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
