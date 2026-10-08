#!/usr/bin/env python3
"""Independent, blind split annotation and frozen development/holdout evaluation.

Called by voiceprint.py split-prepare / spotcheck --split / split-eval. Automatic
clustering is used only to stratify sampling, never as ground truth. No acceptance
artifact is emitted until the holdout has been reviewed by a human.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
import time
import unicodedata
import wave

from local_backend import (ROOT, TEAM, Speakers, commitment_evidence, align_words, resolve_owner,
                           load_jev, voiceprint_decision, split_fingerprints, JEV_URL)
from speaker_validation import (FEATURES, OWNER_FEATURES, NAMED_FEATURES, SEGMENT_EVENT,
    OWNER_EVENT, NAMED_EVENT, fit_logistic, calibrated_probability, calibration_error,
    descriptive_bootstrap, gate_statuses, frozen_holdout_ready, consume_holdout,
    calibrator_hash)

OUT = Path(ROOT) / "models/voiceprint"
FOLDER = OUT / "split-check"
TRUTH = OUT / "split-truth.json"
FROZEN = OUT / "split-config.json"
VALIDATION = OUT / "split-validation.json"
MIN_SELF_HOLDOUT = 15


def dataset_paths(args):
    name = getattr(args, "dataset", None) or "dji"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise ValueError("题集名称只能包含字母、数字、下划线和短横线")
    root = Path(getattr(args, "dataset_root", None) or OUT / "split-datasets")
    base = root / name
    return {"name": name, "root": root, "base": base, "folder": base / "clips",
            "manifest": base / "manifest.json", "truth": base / "truth.json",
            "frozen": base / "frozen.json", "validation": base / "validation.json",
            "ledger": root / "holdout-consumption.json"}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write(path, value):
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=path.name + ".", suffix=".tmp",
                                     dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    tmp.replace(path)


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def recording_purpose(wav):
    sidecar = read(wav.with_suffix(".json"), {})
    classification = read(Path(ROOT) / "evidence/speaker-attribution/recording-classification.json", {})
    entries = classification.get("recordings", {})
    classified = entries.get(str(wav.resolve()), entries.get(wav.name, {}))
    declared = classified if isinstance(classified, str) else classified.get("purpose", "unspecified")
    adjacent = sidecar.get("purpose", "unspecified")
    explicit = {value for value in (declared, adjacent) if value != "unspecified"}
    if len(explicit) > 1:
        raise ValueError("录音邻接元数据与用途分类表冲突")
    return next(iter(explicit), "unspecified")


def validate_recording_purpose(wav, partition, dataset):
    if dataset == "dji":
        return "historical-dji"
    purpose = recording_purpose(wav)
    expected = "meeting-development" if partition == "dev" else "meeting-holdout"
    if purpose != expected:
        raise ValueError("录音用途不允许进入该集合：" + wav.name + " (" + purpose + "; expected " + expected + ")")
    return purpose


def corpus(args=None):
    dev = list(getattr(args, "dev_recording", None) or [])
    holdout = list(getattr(args, "holdout_recording", None) or [])
    requested = {Path(value).name: value for value in dev + holdout}
    if len(requested) != len(dev + holdout):
        raise ValueError("开发和保留场次必须独立；录音名称不能重复")
    if bool(dev) != bool(holdout):
        raise ValueError("必须同时显式指定开发场次和保留场次")
    dataset = getattr(args, "dataset", None) or "dji"
    if not dev and dataset != "dji":
        raise ValueError("实际链路题集需显式 --dev-recording / --holdout-recording")
    found = {}
    for summary in sorted((Path(ROOT) / "evidence").glob("asr-words*/**/summary.json")):
        report = read(summary)
        if not report.get("complete") or report.get("transport_errors"):
            continue
        recording = report["recording"]
        if requested and recording not in requested:
            continue
        if not requested and "_0236_" not in recording and "_0238_" not in recording:
            continue
        source = summary.parent / "finals.json"
        if recording not in found or source.stat().st_mtime > found[recording].stat().st_mtime:
            found[recording] = source
    if requested and set(found) != set(requested) or not requested and len(found) != 2:
        raise FileNotFoundError("需要已指定场次的完整、无传输错误 ASR 结果；先完成 asr_words_probe.py")
    rows = []
    for recording, source in sorted(found.items()):
        raw_path = Path(requested.get(recording, recording))
        wav = raw_path if raw_path.is_absolute() else Path(getattr(args, "recordings_root", None) or Path(ROOT) / "test-media") / raw_path
        if not wav.is_file():
            raise FileNotFoundError("未找到实录音频：" + str(wav))
        partition = "dev" if recording in {Path(value).name for value in dev} or not dev and "_0236_" in recording else "holdout"
        purpose = validate_recording_purpose(wav, partition, dataset)
        hasher = hashlib.sha256()
        with wav.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        wav_hash = hasher.hexdigest()
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
            rows.append({"id": hashlib.sha256(recording.encode()).hexdigest()[:12] + f"-u{i + 1}", "recording": recording,
                         "partition": partition, "purpose": purpose,
                         "audio_path": str(wav.resolve()), "recording_hash": wav_hash,
                         "start_ms": start, "end_ms": end, "text": u["text"], "words": words,
                         "boundaries": boundaries, "approx": approx, "coverage": coverage,
                         "context": [{"text": old["text"], "speaker": None} for old in utterances[max(0, i - 4):i]]})
    return rows


def validate_partitions(manifest):
    scenes, audio = {}, {}
    ids = set()
    for case in manifest.get("cases", []):
        if case.get("partition") not in ("dev", "holdout") or not case.get("recording") or case.get("id") in ids:
            raise ValueError("无效场次划分或重复句子编号")
        ids.add(case["id"])
        part, rec = case["partition"], case["recording"]
        if rec in scenes and scenes[rec] != part:
            raise ValueError("同一会议不能同时属于开发和保留集")
        scenes[rec] = part
        hash_ = case.get("recording_hash")
        if hash_:
            if hash_ in audio and audio[hash_] != part:
                raise ValueError("相同音频不能换名后跨开发/保留集")
            audio[hash_] = part
    if set(scenes.values()) != {"dev", "holdout"}:
        raise ValueError("需要至少一个独立开发场次和一个独立保留场次")
    return scenes


def import_legacy_dji(paths):
    """Copy the existing frozen 55-case set without changing its IDs or original files."""
    import shutil
    old = read(FOLDER / "manifest.json")
    if not old:
        return False
    manifest = dict(old)
    manifest.pop("hash", None)
    cases, hashes = [], {}
    paths["folder"].mkdir(parents=True, exist_ok=True)
    for original in old["cases"]:
        case = dict(original)
        wav = Path(ROOT) / "test-media" / case["recording"]
        if wav.is_file():
            if case["recording"] not in hashes:
                hasher = hashlib.sha256()
                with wav.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        hasher.update(chunk)
                hashes[case["recording"]] = hasher.hexdigest()
            case.update(audio_path=str(wav.resolve()), recording_hash=hashes[case["recording"]])
        old_clip = FOLDER / case["clip"]
        if not old_clip.is_file() or not old_clip.resolve().is_relative_to(FOLDER.resolve()):
            raise ValueError("旧冻结题集素材缺失或路径无效")
        clip = Path(case["clip"]).name
        shutil.copy2(old_clip, paths["folder"] / clip)
        case["clip"] = "clips/" + clip
        case["clip_hash"] = hashlib.sha256((paths["folder"] / clip).read_bytes()).hexdigest()
        cases.append(case)
    manifest.update(schema=2, dataset="dji", cases=cases, source="DJI", acceptance_scope="historical-dji",
                    sampling="Imported unchanged legacy frozen 55-case stratified selection",
                    legacy_manifest_hash=old["hash"],
                    limitations="Selected sample; within-meeting sentences may be correlated.")
    validate_partitions(manifest)
    manifest["hash"] = digest(manifest)
    write(paths["manifest"], manifest)
    old_truth = read(TRUTH)
    if old_truth and old_truth.get("manifest_hash") == old["hash"]:
        answers = {key: dict(answer) for key, answer in old_truth.get("answers", {}).items()}
        for answer in answers.values():
            if not answer.get("reviewed") and answer.get("self_present") is None and not answer.get("self_owner"):
                answer.setdefault("commitments", [])
        write(paths["truth"], old_truth | {"manifest_hash": manifest["hash"], "legacy_manifest_hash": old["hash"], "answers": answers})
    print(json.dumps({"status": "imported_legacy", "dataset": "dji", "cases": len(cases),
                      "original_files_preserved": True, "manifest": str(paths["manifest"])}, ensure_ascii=False))
    return True


def prepare(args):
    paths = dataset_paths(args)
    if paths["manifest"].exists():
        print("复用已有冻结标注集（不覆盖人工答案）：", paths["manifest"])
        return
    if (paths["name"] == "dji" and not getattr(args, "dev_recording", None)
            and not getattr(args, "holdout_recording", None)
            and (getattr(args, "count", None) or 55) == 55 and import_legacy_dji(paths)):
        return
    try:
        pool = corpus(args)
    except FileNotFoundError as exc:
        print(json.dumps({"status": "pending_recordings", "reason": str(exc)}, ensure_ascii=False))
        return
    validate_partitions({"cases": pool})
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
    target_count = int(getattr(args, "count", None) or 55)
    if target_count < 2:
        raise ValueError("题集至少需要两句且覆盖开发/保留场次")
    def hints(u):
        parts = u["recording"].split("_")
        return turns.get(u["recording"], turns.get(parts[2] if len(parts) > 2 else "", []))
    categories = [("self", max(1, target_count * 15 // 55), lambda u: commitment_evidence(u["text"])[0] == "self"),
                  ("short_or_turn", target_count * 10 // 55, lambda u: any(u["start_ms"] - 1500 <= t <= u["end_ms"] + 1500 for t in hints(u))),
                  ("long", target_count * 15 // 55, lambda u: u["end_ms"] - u["start_ms"] > 6000),
                  ("possible_overlap", target_count * 5 // 55, lambda u: sum(u["start_ms"] <= t <= u["end_ms"] for t in hints(u)) >= 3),
                  ("random", target_count * 10 // 55, lambda u: True)]
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
    chosen.extend(dict(u, category="random_fill") for u in rest[:max(0, target_count - len(chosen))])
    # Every explicit scene must remain represented even in a small stratified sample.
    for recording in sorted({u["recording"] for u in pool} - {u["recording"] for u in chosen}):
        chosen.append(dict(next(u for u in pool if u["recording"] == recording), category="scene_coverage"))
    paths["folder"].mkdir(parents=True, exist_ok=True)
    for u in chosen:
        clip = u["id"] + ".wav"
        with wave.open(u["audio_path"]) as source:
            if source.getparams()[:3] != (1, 2, 16000):
                raise ValueError("实录必须为16kHz/mono/PCM16 WAV")
            source.setpos(u["start_ms"] * 16)
            pcm = source.readframes((u["end_ms"] - u["start_ms"]) * 16)
            # The provider can include final transport silence beyond the original WAV.
            pcm = pcm.ljust((u["end_ms"] - u["start_ms"]) * 32, b"\0")
        with wave.open(str(paths["folder"] / clip), "wb") as dest:
            dest.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            dest.writeframes(pcm)
        u["clip"] = "clips/" + clip
        u["clip_hash"] = hashlib.sha256((paths["folder"] / clip).read_bytes()).hexdigest()
    manifest = {"schema": 2, "dataset": paths["name"], "seed": args.seed,
                "acceptance_scope": "historical-dji" if paths["name"] == "dji" else "actual-chain",
                "source": getattr(args, "source", None) or ("DJI" if paths["name"] == "dji" else "actual-chain"),
                "cases": sorted(chosen, key=lambda u: (u["recording"], u["start_ms"])),
                "partition_rule": "explicit recording-level development/holdout",
                "sampling": "seeded stratified sampling; hints are not truth",
                "limitations": "Selected sample; within-meeting sentences may be correlated."}
    validate_partitions(manifest)
    manifest["hash"] = digest(manifest)
    write(paths["manifest"], manifest)
    print(json.dumps({"cases": len(chosen), "dev": sum(u["partition"] == "dev" for u in chosen),
                      "holdout": sum(u["partition"] == "holdout" for u in chosen), "manifest": str(paths["manifest"])}, ensure_ascii=False))


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Oops 盲标注</title>
<style>body{font:16px/1.7 system-ui;background:#f4f4fa;color:#191526;margin:0}main{max-width:960px;margin:auto;padding:24px 24px 100px}.case{background:white;padding:18px;border-radius:12px;margin:18px 0}button,input,select{font:inherit}.text button{padding:2px;border:0;background:transparent}.text .cut{border-right:3px solid #7255ee}.segment{margin:12px 0}label{margin-right:12px}audio{width:100%}.bar{position:fixed;bottom:0;background:white;padding:14px;width:100%;box-sizing:border-box}.commit{padding:10px;border:1px solid #ddd;margin:8px 0}.commit input[type=number]{width:70px}small{color:#666}</style>
<main><h1>独立说话人盲标注</h1><label>题集 <select id="dataset"></select></label><p>先听原音，再按文字边界切段并标真实说话人。多人同时说可选多人。承诺逐条标字符区间、类型、实际负责人及对应行动区间；字符区间使用从 0 开始、右端不包含的编号。页面不显示模型身份或候选。没有承诺则不添加。</p><div id="list"></div></main><div class="bar"><button id="save">保存当前题集</button> <span id="status"></span></div>
<script>
let manifest,truth={},dataset='';const team=__TEAM__;const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const base=()=>'/dataset/'+encodeURIComponent(dataset)+'/';
async function load(name){dataset=name;const [m,t]=await Promise.all([fetch(base()+'manifest').then(r=>r.json()),fetch(base()+'truth').then(r=>r.json())]);manifest=m;truth=t.answers||{};render();}
fetch('/datasets').then(r=>r.json()).then(data=>{const sel=document.getElementById('dataset');sel.innerHTML=data.datasets.map(d=>`<option value="${esc(d.name)}">${esc(d.name)} (${d.cases} 句)</option>`).join('');sel.value=data.selected;sel.onchange=()=>load(sel.value);if(sel.value)load(sel.value);});
function answer(u){return truth[u.id]||(truth[u.id]={cuts:[],segments:[{speakers:[]}],commitments:[],reviewed:false});}
function render(){const list=document.getElementById('list');list.innerHTML='';manifest.cases.forEach((u,n)=>{
 const a=answer(u),d=document.createElement('section');d.className='case';const allowed=new Set(u.boundaries.map(b=>b.char));
 d.innerHTML=`<h3>${n+1}. ${esc(u.id)} · ${u.partition==='dev'?'开发集':'保留集'}</h3><audio controls preload="none" src="${base()}${esc(u.clip)}"></audio><p class="text">${Array.from(u.text).map((c,i)=>`<button title="字符 ${i}" data-cut="${i+1}" class="${a.cuts.includes(i+1)?'cut':''}" ${allowed.has(i+1)?'':'disabled'}>${esc(c)}</button>`).join('')}</p>`;
 const points=[0,...a.cuts,Array.from(u.text).length];points.slice(0,-1).forEach((start,i)=>{const seg=a.segments[i]||(a.segments[i]={speakers:[]});const box=document.createElement('div');box.className='segment';box.innerHTML=`<div>[${start}, ${points[i+1]}) ${esc(Array.from(u.text).slice(start,points[i+1]).join(''))}</div>`+[...team,'未注册人员','听不出'].map(name=>`<label><input type="checkbox" data-segment="${i}" value="${esc(name)}" ${seg.speakers.includes(name)?'checked':''}>${esc(name)}</label>`).join('');d.appendChild(box);});
 if(!Array.isArray(a.commitments)){const old=document.createElement('p');old.textContent='旧单承诺标注保持只读兼容；如需重新标注，请使用新题集。';d.appendChild(old);}else{
 a.commitments.forEach((c,i)=>{const box=document.createElement('div');box.className='commit';box.innerHTML=`承诺 [<input type="number" min="0" max="${Array.from(u.text).length}" data-ci="${i}" data-field="start" value="${c.start}">,<input type="number" min="0" max="${Array.from(u.text).length}" data-ci="${i}" data-field="end" value="${c.end}">) <select data-ci="${i}" data-field="kind">${[['self','本人'],['named','点名'],['none','不是承诺']].map(([k,l])=>`<option value="${k}" ${c.kind===k?'selected':''}>${l}</option>`).join('')}</select> <select data-ci="${i}" data-field="owner"><option value="">待定</option>${team.map(name=>`<option ${c.owner===name?'selected':''}>${esc(name)}</option>`).join('')}</select><br>对应行动 [<input type="number" min="0" max="${Array.from(u.text).length}" data-ci="${i}" data-field="action_start" value="${c.action_start}">,<input type="number" min="0" max="${Array.from(u.text).length}" data-ci="${i}" data-field="action_end" value="${c.action_end}">) <small>${esc(Array.from(u.text).slice(c.action_start,c.action_end).join(''))}</small> <button data-remove="${i}">删除</button>`;d.appendChild(box);});
 const add=document.createElement('button');add.textContent='添加一条承诺/指派';add.dataset.add='';d.appendChild(add);}
 const reviewed=document.createElement('p');reviewed.innerHTML=`<label><input type="checkbox" data-reviewed ${a.reviewed?'checked':''}>已听过并核实说话人及全部承诺</label>`;d.appendChild(reviewed);
 d.onclick=e=>{const cut=e.target.closest('[data-cut]');if(cut&&!cut.disabled){const at=Number(cut.dataset.cut);a.cuts=a.cuts.includes(at)?a.cuts.filter(c=>c!==at):[...a.cuts,at].sort((x,y)=>x-y);a.segments=Array.from({length:a.cuts.length+1},()=>({speakers:[]}));a.reviewed=false;render();}else if(e.target.hasAttribute('data-add')){a.commitments.push({start:0,end:Array.from(u.text).length,kind:'self',owner:null,action_start:0,action_end:Array.from(u.text).length});a.reviewed=false;render();}else if(e.target.hasAttribute('data-remove')){a.commitments.splice(Number(e.target.dataset.remove),1);a.reviewed=false;render();}};
 d.onchange=e=>{if(e.target.dataset.segment!==undefined){const seg=a.segments[Number(e.target.dataset.segment)];seg.speakers=e.target.checked?[...new Set([...seg.speakers,e.target.value])]:seg.speakers.filter(v=>v!==e.target.value);a.reviewed=false;}else if(e.target.dataset.ci!==undefined){const c=a.commitments[Number(e.target.dataset.ci)],field=e.target.dataset.field;c[field]=field.endsWith('start')||field.endsWith('end')?Number(e.target.value):e.target.value||null;a.reviewed=false;}else if(e.target.hasAttribute('data-reviewed'))a.reviewed=e.target.checked;count();};list.appendChild(d);
 });count();}
function count(){document.getElementById('status').textContent=`已核实 ${Object.values(truth).filter(a=>a.reviewed).length}/${manifest.cases.length} 句`;}
document.getElementById('save').onclick=()=>fetch(base()+'save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({manifest_hash:manifest.hash,answers:truth})}).then(async r=>{document.getElementById('status').textContent=r.ok?'已保存当前题集':await r.text();});
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
        if not isinstance(cuts, list) or any(type(c) is not int for c in cuts) or cuts != sorted(set(cuts)) or any(c not in allowed for c in cuts):
            raise ValueError("切点必须位于可对齐词边界")
        segments = a.get("segments", [])
        if not isinstance(segments, list) or len(segments) != len(cuts) + 1:
            raise ValueError("分段数量不匹配")
        for s in segments:
            if not isinstance(s, dict) or not isinstance(s.get("speakers"), list) or any(n not in TEAM + ["未注册人员", "听不出"] for n in s["speakers"]):
                raise ValueError("无效说话人")
            if len(set(s["speakers"])) != len(s["speakers"]) or ("听不出" in s["speakers"] and len(s["speakers"]) != 1):
                raise ValueError("听不出不能同时标为已知说话人，名单不能重复")
        modern = "commitments" in a
        if modern:
            commitments = a["commitments"]
            if not isinstance(commitments, list):
                raise ValueError("承诺标注必须逐条保存")
            seen = set()
            for span in commitments:
                if not isinstance(span, dict):
                    raise ValueError("无效承诺标注")
                kind = span.get("kind", span.get("type"))
                for start, end in (("start", "end"), ("action_start", "action_end")):
                    if (type(span.get(start)) is not int or type(span.get(end)) is not int
                            or not 0 <= span[start] < span[end] <= len(u["text"])):
                        raise ValueError("承诺和行动绑定必须为原文字区间")
                signature = (span["start"], span["end"], span["action_start"], span["action_end"])
                if signature in seen or kind not in ("self", "named", "none"):
                    raise ValueError("承诺不能重复或缺少类型")
                seen.add(signature)
                if a.get("reviewed") and kind != "none" and span.get("owner") not in TEAM:
                    raise ValueError("已核实承诺必须标实际负责人")
        if a.get("reviewed") and (any(not s["speakers"] for s in segments) or not modern and
                (a.get("self_present") not in (True, False) or a["self_present"] and a.get("self_owner") not in TEAM)):
            raise ValueError(f"{key} 已核实但缺少说话人或承诺人标注")
    return body


def serve(args):
    from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
    from urllib.parse import urlsplit, unquote
    from types import SimpleNamespace
    paths = dataset_paths(args)
    if not paths["manifest"].exists():
        prepare(args)
    if not paths["manifest"].exists():
        return
    page = PAGE.replace("__TEAM__", json.dumps(TEAM, ensure_ascii=False)).encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send_data(self, data, content_type="application/json; charset=utf-8"):
            self.send_response(200); self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def route(self):
            parts = [unquote(v) for v in urlsplit(self.path).path.split("/") if v]
            if len(parts) < 3 or parts[0] != "dataset":
                return None, None
            try:
                selected = dataset_paths(SimpleNamespace(dataset=parts[1], dataset_root=paths["root"]))
            except ValueError:
                return None, None
            return selected, "/".join(parts[2:])

        def do_GET(self):
            route = urlsplit(self.path).path
            if route in ("/", "/index.html"):
                return self.send_data(page, "text/html; charset=utf-8")
            if route == "/datasets":
                inventory = [{"name": p.parent.name, "cases": len(read(p).get("cases", []))}
                             for p in sorted(paths["root"].glob("*/manifest.json"))]
                return self.send_data(json.dumps({"selected": paths["name"], "datasets": inventory}, ensure_ascii=False).encode())
            selected, endpoint = self.route()
            if selected is None or not selected["manifest"].is_file():
                return self.send_error(404)
            manifest = read(selected["manifest"])
            if endpoint == "manifest":
                # Audio paths and hashes are neither identity predictions nor web resources.
                public = manifest | {"cases": [{k: v for k, v in u.items() if k != "audio_path"} for u in manifest["cases"]]}
                return self.send_data(json.dumps(public, ensure_ascii=False).encode())
            if endpoint == "truth":
                return self.send_data(json.dumps(read(selected["truth"], {"manifest_hash": manifest["hash"], "answers": {}}), ensure_ascii=False).encode())
            if endpoint in {u["clip"] for u in manifest["cases"]}:
                clip = selected["base"] / endpoint
                if clip.is_file() and clip.resolve().is_relative_to(selected["base"].resolve()):
                    return self.send_data(clip.read_bytes(), "audio/wav")
            self.send_error(404)

        def do_POST(self):
            selected, endpoint = self.route()
            if selected is None or endpoint != "save" or not selected["manifest"].exists():
                return self.send_error(404)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 1_000_000:
                    raise ValueError("标注体过大或为空")
                incoming = json.loads(self.rfile.read(size))
                with evaluation_lock(selected["base"]):
                    if read(selected["validation"], {}).get("holdout_consumed"):
                        raise ValueError("最终保留集已消费，真值只读；新标注请建立新题集")
                    body = validated_truth(incoming, read(selected["manifest"]))
                    write(selected["truth"], body)
                self.send_response(204); self.end_headers()
                print("已保存人工核实句数：", sum(a.get("reviewed", False) for a in body["answers"].values()), flush=True)
            except (ValueError, TypeError, KeyError) as exc:
                data = str(exc).encode("utf-8")
                self.send_response(400); self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    print(f"独立标注页面：http://127.0.0.1:{args.port}/ （可切换题集；不显示模型预测）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


def gold_segments(u, answer):
    points = [0] + answer["cuts"] + [len(u["text"])]
    times = {b["char"]: b["time_ms"] for b in u["boundaries"]} | {0: u["start_ms"], len(u["text"]): u["end_ms"]}
    return [{"text": u["text"][a:b], "char_start": a, "char_end": b, "start_ms": times[a], "end_ms": times[b],
             "speakers": s["speakers"]} for a, b, s in zip(points, points[1:], answer["segments"])]


def visible_char(char):
    return not char.isspace() and not unicodedata.category(char).startswith("P")


def gold_characters(case, answer):
    labels = [None] * len(case["text"])
    for span in gold_segments(case, answer):
        for index in range(span["char_start"], span["char_end"]):
            if visible_char(case["text"][index]) and "听不出" not in span["speakers"]:
                labels[index] = tuple(span["speakers"])
    return labels


def prediction_spans(case, prediction):
    if "".join(segment.get("text", "") for segment in prediction) != case["text"]:
        raise ValueError("预测未保留完整原文")
    offset, result = 0, []
    for segment in prediction:
        end = offset + len(segment["text"])
        result.append((offset, end, segment))
        offset = end
    return result


def segment_records(case, answer, prediction):
    from speaker_attribution import score_features
    labels, result = gold_characters(case, answer), []
    for start, end, segment in prediction_spans(case, prediction):
        indices = [i for i in range(start, end) if labels[i] is not None and len(labels[i]) == 1]
        if not indices or not segment.get("speaker") or segment.get("overlap") or segment.get("approx") is not False:
            continue
        correct = sum(labels[i][0] == segment["speaker"] for i in indices)
        candidates = segment.get("candidates") or []
        features = segment.get("_features") or score_features(
            [c["score"] for c in candidates], [c["name"] for c in candidates],
            segment["speaker"], segment["end_ms"] - segment["start_ms"], False,
            segment.get("source") == "voiceprint+context")
        result.append({"features": features, "correct": int(correct / len(indices) >= .9),
                       "segment": segment, "start": start, "end": end, "chars": len(indices)})
    return result


def gold_commitments(case, answer):
    if "commitments" in answer:
        return [dict(span, kind=span.get("kind", span.get("type")), binding_verified=True)
                for span in answer["commitments"] if span.get("kind", span.get("type")) != "none"]
    # Read-only compatibility: an old single owner cannot verify action binding or multiple spans.
    from local_backend import commitment_spans
    spans = commitment_spans(case["text"])
    if answer.get("self_present") and len(spans) == 1 and spans[0]["kind"] == "self":
        return [spans[0] | {"owner": answer["self_owner"], "binding_verified": False}]
    return []


def named_features(span, single_relation=True):
    return {"span_chars": float(span["end"] - span["start"]), "explicit_named": 1.0,
            "single_relation": float(single_relation)}


def commitment_records(case, answer, prediction, config=None):
    from local_backend import commitment_spans, span_segment, owner_resolution
    from speaker_attribution import owner_features
    config = config or {}
    spans, gold = commitment_spans(case["text"]), gold_commitments(case, answer)
    live = owner_resolution({"utterances": [{"text": case["text"], "segments": prediction}]},
                            config, strict_enabled=True, self_enabled=True, named_enabled=True)
    labels, result = gold_characters(case, answer), []
    for span in spans:
        kind = span["kind"]
        if kind not in ("self", "named"):
            continue
        expected = next((g for g in gold if g["kind"] == kind and g["start"] == span["start"] and g["end"] == span["end"]), None)
        if kind == "self":
            segment, _ = span_segment({"text": case["text"], "segments": prediction}, span)
            if segment is None:
                continue
            name, features = segment["speaker"], owner_features(segment, span["start"], span["end"], len(case["text"]))
            probability = calibrated_probability(config.get("owner_calibrator"), features, OWNER_EVENT)
            eligible = live.get("tier") == "A" and live.get("owner") == name
            spoken = [labels[i] for i in range(span["start"], span["end"]) if visible_char(case["text"][i])]
            correct_audio = bool(spoken) and all(value == (name,) for value in spoken)
        else:
            name, features = span["owner"], named_features(span, len(spans) == 1)
            probability = calibrated_probability(config.get("named_calibrator"), features, NAMED_EVENT)
            eligible = live.get("tier") == "A" and live.get("owner") == name
            correct_audio = True
        binding = bool(expected and expected.get("binding_verified") and
                       span["start"] <= expected["action_start"] < expected["action_end"] <= span["end"])
        correct = bool(binding and expected["owner"] == name and correct_audio)
        result.append({"kind": kind, "features": features, "correct": int(correct),
                       "probability": probability, "eligible": eligible, "speaker": name,
                       "span": span, "gold": expected})
    return result


def metrics(cases, answers, predictions, jev=None, config=None):
    total = correct = claimed = wrong = overlap_total = overlap_correct = 0
    boundaries = hits = false_points = short_total = short_correct = short_wrong = 0
    a_count = a_wrong = a_sentences = a_sentence_wrong = a_chars = 0
    tier_counts = {tier: {"chars": 0, "correct": 0, "claimed": 0, "wrong": 0} for tier in ("A", "B", "C")}
    owners = {kind: {"count": 0, "claimed": 0, "correct": 0, "wrong": 0} for kind in ("self", "named")}
    calibration, owner_calibration, sentence_counts = [], {"self": [], "named": []}, []
    for u in cases:
        a, pred = answers[u["id"]], predictions[u["id"]]
        gold, labels = gold_segments(u, a), gold_characters(u, a)
        spans = prediction_spans(u, pred)
        by_char = [segment for start, end, segment in spans for _ in range(start, end)]
        names = [segment.get("speaker") for segment in by_char]
        sentence_wrong = sentence_claimed = 0
        for s in gold:
            if "听不出" in s["speakers"]:
                continue
            indices = [i for i in range(s["char_start"], s["char_end"]) if visible_char(u["text"][i])]
            if len(s["speakers"]) > 1:
                overlap_total += len(indices)
                for i in indices:
                    candidates = {c.get("name") for c in by_char[i].get("candidates", [])[:2] if isinstance(c, dict)}
                    overlap_correct += bool(candidates.intersection(s["speakers"]))
                continue
            target = s["speakers"][0]
            total += len(indices)
            correct += sum(names[i] == target for i in indices)
            claimed += sum(names[i] is not None for i in indices)
            span_wrong = sum(names[i] is not None and names[i] != target for i in indices)
            wrong += span_wrong
            sentence_wrong += span_wrong
            sentence_claimed += sum(names[i] is not None for i in indices)
            for i in indices:
                tier = by_char[i].get("tier", "B" if names[i] else "C")
                if tier not in tier_counts:
                    tier = "C"
                row = tier_counts[tier]
                row["chars"] += 1
                row["correct"] += names[i] == target
                row["claimed"] += names[i] is not None
                row["wrong"] += names[i] is not None and names[i] != target
            if s["end_ms"] - s["start_ms"] < 1500 and indices:
                short_total += 1
                short_correct += sum(names[i] == target for i in indices) > len(indices) / 2
                counts = {n: sum(names[i] == n for i in indices) for n in set(names[i] for i in indices) if n is not None and n != target}
                short_wrong += bool(counts and max(counts.values()) > len(indices) / 2)
        sentence_counts.append((sentence_wrong, sentence_claimed))
        events = segment_records(u, a, pred)
        active_a = [r for r in events if r["segment"].get("tier") == "A"]
        # Invalid A claims are failures, including overlap/approx/unknown and no eligible text.
        invalid_a = sum(segment.get("tier") == "A" and not any(r["segment"] is segment for r in active_a) for _, _, segment in spans)
        if active_a or invalid_a:
            a_sentences += 1
            a_sentence_wrong += bool(invalid_a or any(not r["correct"] for r in active_a))
        a_count += len(active_a) + invalid_a
        a_wrong += sum(not r["correct"] for r in active_a) + invalid_a
        a_chars += sum(r["chars"] for r in active_a)
        calibration.extend((r["segment"].get("confidence"), r["correct"]) for r in events)
        gt = [b["start_ms"] for a_, b in zip(gold, gold[1:]) if len(a_["speakers"]) == len(b["speakers"]) == 1
              and "听不出" not in a_["speakers"] + b["speakers"] and a_["speakers"] != b["speakers"]]
        pp = [b["start_ms"] for a_, b in zip(pred, pred[1:]) if a_.get("speaker") != b.get("speaker")]
        excluded = [s for s in gold if len(s["speakers"]) > 1 or "听不出" in s["speakers"]]
        available = [at for at in pp if not any(s["start_ms"] <= at <= s["end_ms"] for s in excluded)]
        boundaries += len(gt)
        for at in gt:
            near = min(available, key=lambda p: abs(p - at)) if available else None
            if near is not None and abs(near - at) <= 500:
                hits += 1
                available.remove(near)
        false_points += len(available)
        for gold_span in gold_commitments(u, a):
            owners[gold_span["kind"]]["count"] += 1
        for record in commitment_records(u, a, pred, config):
            owner_calibration[record["kind"]].append((record["probability"], record["correct"]))
            if record["eligible"]:
                row = owners[record["kind"]]
                row["claimed"] += 1
                row["correct"] += record["correct"]
                row["wrong"] += not record["correct"]
    result = {"char_accuracy": correct / max(1, total), "wrong_claim_rate": wrong / max(1, claimed),
              "unknown_rate": (total - claimed) / max(1, total), "chars": total, "claimed_chars": claimed,
              "change_recall": hits / max(1, boundaries), "change_count": boundaries, "change_hits": hits,
              "false_change_points": false_points, "short_recall": short_correct / max(1, short_total),
              "short_wrong_rate": short_wrong / max(1, short_total), "short_count": short_total,
              "overlap_chars": overlap_total, "overlap_any_candidate_accuracy": overlap_correct / max(1, overlap_total),
              "overlap_any_speaker_accuracy": overlap_correct / max(1, overlap_total),
              "a_segment_count": a_count, "a_segment_wrong": a_wrong,
              "a_sentence_count": a_sentences, "a_sentence_wrong": a_sentence_wrong,
              "a_char_coverage": a_chars / max(1, total), "a_coverage": a_chars / max(1, total),
              "calibration_error": calibration_error(calibration),
              "bootstrap": descriptive_bootstrap(sentence_counts),
              "tiers": {tier: counts | {"char_accuracy": counts["correct"] / max(1, counts["chars"])} for tier, counts in tier_counts.items()}}
    for kind, row in owners.items():
        result.update({kind + "_" + key: value for key, value in row.items()})
        result[kind + "_coverage"] = row["claimed"] / max(1, row["count"])
        result[kind + "_accuracy"] = row["correct"] / max(1, row["claimed"])
        result[kind + "_calibration_error"] = calibration_error(owner_calibration[kind])
    return result


def gates(final, baseline):
    """Compatibility tuple, with strict independent owner evidence replacing the old gate."""
    statuses = gate_statuses(final, baseline)
    return statuses["display"] == "pass", statuses["self_owner"] == "pass"


def apply_calibration(cases, predictions, config):
    from speaker_attribution import tier_for
    for case in cases:
        for segment in predictions[case["id"]]:
            probability = calibrated_probability(config.get("calibrator"), segment.get("_features") or {}, SEGMENT_EVENT) \
                if segment.get("speaker") and not segment.get("overlap") and segment.get("approx") is False else None
            segment["confidence"] = probability
            segment["tier"] = tier_for(segment.get("speaker"), probability, segment.get("approx", True), segment.get("overlap", False), config | {"_evaluation": True})
    return predictions


def pending(status, reason, **extra):
    result = {"status": status, "reason": reason, **extra}
    print(json.dumps(result, ensure_ascii=False))
    return result


def evaluation_lock(base):
    """Serialize final looks and annotation writes for the same local dataset."""
    import contextlib
    import fcntl
    @contextlib.contextmanager
    def locked():
        base.mkdir(parents=True, exist_ok=True)
        with (base / "evaluation.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
    return locked()


def journal_consumption(path, recordings, version, frozen_hash, truth_hash):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            ledger = read(path, {"schema": 1, "recordings": {}})
            repeated = consume_holdout(ledger, recordings, version, frozen_hash, truth_hash)
            write(path, ledger)
            return repeated
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def speaker_prior(segment, config):
    confidence = segment.get("confidence")
    if (segment.get("speaker") in TEAM and segment.get("approx") is False and not segment.get("overlap")
            and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and math.isfinite(confidence) and confidence >= (config.get("thresholds") or {}).get("a", .98)):
        return segment["speaker"]
    return None


def evaluate(args):
    with evaluation_lock(dataset_paths(args)["base"]):
        return _evaluate(args)


def _evaluate(args):
    paths = dataset_paths(args)
    manifest, truth = read(paths["manifest"]), read(paths["truth"])
    if not manifest or not truth or truth.get("manifest_hash") != manifest["hash"]:
        return pending("pending_human", "待独立实录与人工标注；未生成验收文件")
    validate_partitions(manifest)
    validated_truth(truth, manifest)
    answers = truth["answers"]
    parts = {part: [u for u in manifest["cases"] if u["partition"] == part] for part in ("dev", "holdout")}
    tune = bool(getattr(args, "tune", False))
    if tune:
        if paths["frozen"].exists():
            raise ValueError("已有冻结版本，不覆盖或在查看保留结果后重调")
        if any(not answers.get(u["id"], {}).get("reviewed") for u in parts["dev"]):
            return pending("pending_human", "开发集尚未完成独立人工标注")
        version = getattr(args, "version", None)
        if not version or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", version):
            raise ValueError("冻结必须指定有效 --version")
        recordings = sorted({u["recording"] for u in parts["holdout"]})
        required_recordings = getattr(args, "holdout_recording_count", None)
        if required_recordings is not None and required_recordings != len(recordings):
            raise ValueError("截止场次必须为已指定的完整场次清单，不临时追加或删除")
        requested_cases = getattr(args, "holdout_case_count", None)
        minimum_cases = len(parts["holdout"]) if requested_cases is None else int(requested_cases)
        if minimum_cases <= 0:
            raise ValueError("最终保留集目标必须大于0")
        cutoff = getattr(args, "holdout_cutoff", None)
        if cutoff:
            import datetime as dt
            parsed = dt.datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("冻结截止时间必须带时区")
        policy = {"mode": "fixed-final-look", "recordings": recordings, "minimum_cases": minimum_cases,
                  "minimum_self": int(getattr(args, "holdout_self_count", 0) or 0),
                  "minimum_named": int(getattr(args, "holdout_named_count", 0) or 0), "cutoff": cutoff,
                  "independence": "Conditional Bernoulli model; sentences within one meeting need not be independent."}
    else:
        frozen = read(paths["frozen"])
        if not frozen or frozen.get("schema") != 2 or frozen.get("manifest_hash") != manifest["hash"]:
            return pending("pending_freeze", "先用开发集运行 split-eval --tune 冻结版本和最终查看规则")
        if getattr(args, "version", None) and args.version != frozen["version"]:
            raise ValueError("验收必须使用冻结版本；新版本需要新独立保留场次")
        if frozen["dev_truth_hash"] != digest({u["id"]: answers.get(u["id"]) for u in parts["dev"]}):
            raise ValueError("冻结后开发标注已改变，需要新版本")
        ready, reason = frozen_holdout_ready(manifest, answers, frozen["holdout_policy"])
        if not ready:
            return pending("insufficient_evidence", reason, predictions_inspected=False, holdout_consumed=False)
        cutoff = frozen["holdout_policy"].get("cutoff")
        if cutoff:
            import datetime as dt
            if dt.datetime.now(dt.timezone.utc) < dt.datetime.fromisoformat(cutoff.replace("Z", "+00:00")):
                return pending("insufficient_evidence", "尚未到冻结最终查看时点", predictions_inspected=False, holdout_consumed=False)
    snapshot = getattr(args, "registry_snapshot", None)
    if not tune:
        snapshot = snapshot or frozen.get("registry_snapshot")
    if paths["name"] != "dji" and not snapshot:
        return pending("pending_registry", "实际链路验收需 --registry-snapshot 指定线上声纹快照")
    if snapshot and not Path(snapshot).is_file():
        return pending("pending_registry", "线上声纹快照尚未准备；不回退到历史 enrolled.json")
    speaker = Speakers(args.accept, args.margin, registry_path=snapshot, require_registry=bool(snapshot))
    if not speaker.ready:
        return pending("pending_model", "声纹模型未加载：" + str(speaker.error))
    fingerprints = split_fingerprints(speaker)
    if not tune and frozen["fingerprints"] != fingerprints:
        raise ValueError("代码、模型或线上实际声纹已改变，需要新版本和新独立验收集")
    if not tune:
        report = read(paths["validation"])
        if report and report.get("finalized"):
            if report["frozen_hash"] != digest(frozen) or report["truth_hash"] != digest(truth):
                raise ValueError("已消费的保留集真值或冻结版本改变，不能重新签发")
            print(json.dumps({"status": "reproduced", "report": str(paths["validation"]), "gate_statuses": report["gate_statuses"]}, ensure_ascii=False))
            return report
    original_scores, cache = speaker.scores, {}
    def cached_scores(pcm):
        key = hashlib.sha256(pcm).hexdigest()
        if key not in cache:
            cache[key] = original_scores(pcm)
        return cache[key]
    speaker.scores = cached_scores
    jev = None if getattr(args, "no_jev", False) or not tune and not frozen["config"].get("use_jev") else load_jev(args)
    jev_engines = set()
    if jev:
        for method in ("choice", "choices"):
            original = getattr(jev, method, None)
            if original is None:
                continue
            method_cache = {}
            def cached_call(*arguments, _original=original, _cache=method_cache, **keywords):
                key = digest([arguments, keywords])
                if key not in _cache:
                    _cache[key] = _original(*arguments, **keywords)
                    if isinstance(_cache[key], tuple) and len(_cache[key]) > 1 and isinstance(_cache[key][1], str):
                        jev_engines.add(_cache[key][1])
                return _cache[key]
            setattr(jev, method, cached_call)
    def predict(cases, config, baseline=False):
        predictions, previous = {}, {}
        for index, u in enumerate(sorted(cases, key=lambda row: (row["recording"], row["start_ms"])), 1):
            clip = paths["base"] / u["clip"]
            if not clip.resolve().is_relative_to(paths["base"].resolve()):
                raise ValueError("片段路径无效")
            if u.get("clip_hash") and hashlib.sha256(clip.read_bytes()).hexdigest() != u["clip_hash"]:
                raise ValueError("冻结标注片段的音频已改变；不能用旧真值验收")
            with wave.open(str(clip)) as audio:
                pcm = audio.readframes(audio.getnframes())
            if baseline:
                scores = speaker.scores(pcm)
                name, score = voiceprint_decision(scores, speaker.names, u["end_ms"] - u["start_ms"],
                    config["accept"], config["margin"], config["accept"], config["margin"])
                segments = [{"start_ms": u["start_ms"], "end_ms": u["end_ms"], "text": u["text"], "speaker": name,
                             "score": score, "source": "voiceprint" if name else None, "approx": u["approx"], "tier": "B" if name else "C"}]
            else:
                last = previous.get(u["recording"])
                prior = last[1] if last and 0 <= u["start_ms"] - last[0] <= 1000 else None
                segments, _ = speaker.split(pcm, u["text"], u["words"], u["start_ms"], u["context"],
                    jev if config.get("use_jev") else None, config | {"_evaluation": True}, prior=prior)
                previous[u["recording"]] = (u["end_ms"], speaker_prior(segments[-1], config) if segments else None)
            predictions[u["id"]] = segments
            if index % 10 == 0:
                print(json.dumps({"progress": index, "total": len(cases), "partition": "dev" if tune else "final", "baseline": baseline}), flush=True)
        return predictions
    config = {"accept": args.accept, "margin": args.margin, "short_accept": args.accept + .1,
              "short_margin": args.margin + .05, "switch_penalty": .8, "use_jev": False,
              "calibrator": None, "owner_calibrator": None, "named_calibrator": None,
              "jev_contract": {"url": JEV_URL, "requested_model": getattr(args, "jev_model", "jev-latest"),
                               "observed_engines": [], "pinned": False,
                               "reason": "Provider immutable model version has not been independently established; offline comparison only."},
              "thresholds": {"a": float(getattr(args, "a_threshold", .98)), "b": float(getattr(args, "b_threshold", .5)), "owner_a": .95, "named_a": .95},
              "strict_a_enabled": False, "self_owner_enabled": False, "named_owner_enabled": False}
    if tune:
        dev, results = parts["dev"], []
        for da in (.05, .1, .15):
            for dm in (.025, .05, .075):
                candidate = config | {"thresholds": dict(config["thresholds"]), "short_accept": args.accept + da, "short_margin": args.margin + dm}
                prediction = predict(dev, candidate)
                numbers = metrics(dev, answers, prediction)
                results.append({"config": candidate, "metrics": numbers, "prediction": prediction})
        eligible = [r for r in results if r["metrics"]["short_count"] > 0 and r["metrics"]["short_wrong_rate"] <= .1]
        selected = max(eligible or results, key=lambda r: (r["metrics"]["char_accuracy"], r["metrics"]["short_recall"], -r["metrics"]["wrong_claim_rate"]))
        config, prediction = selected["config"], selected["prediction"]
        if jev:
            alternative = predict(dev, config | {"use_jev": True})
            numbers = metrics(dev, answers, alternative)
            # The provider currently returns a model label, sometimes falling back to a
            # mutable request alias. That is not evidence of an immutable model release.
            # Freeze the acoustic version; report the development comparison separately.
            jev_development = numbers
        else:
            jev_development = None
        config["jev_contract"]["observed_engines"] = sorted(jev_engines)
        segment_samples = [record for u in dev for record in segment_records(u, answers[u["id"]], prediction[u["id"]])]
        config["calibrator"] = fit_logistic([r["features"] for r in segment_samples], [r["correct"] for r in segment_samples])
        apply_calibration(dev, prediction, config)
        owner_samples = [record for u in dev for record in commitment_records(u, answers[u["id"]], prediction[u["id"]], config)]
        for kind, key, features, event in (("self", "owner_calibrator", OWNER_FEATURES, OWNER_EVENT), ("named", "named_calibrator", NAMED_FEATURES, NAMED_EVENT)):
            samples = [r for r in owner_samples if r["kind"] == kind]
            config[key] = fit_logistic([r["features"] for r in samples], [r["correct"] for r in samples], features, event)
        # Threshold selection uses development events only; empty A coverage is not a success.
        choices = sorted({config["thresholds"]["a"], .97, .98, .99})
        threshold_results = []
        for threshold in choices:
            candidate = config | {"thresholds": config["thresholds"] | {"a": threshold}}
            apply_calibration(dev, prediction, candidate)
            numbers = metrics(dev, answers, prediction, config=candidate)
            threshold_results.append((threshold, numbers))
        eligible_thresholds = [(t, m) for t, m in threshold_results if m["a_segment_count"] and m["a_segment_wrong"] / m["a_segment_count"] <= .02]
        if eligible_thresholds:
            config["thresholds"]["a"] = max(eligible_thresholds, key=lambda item: (item[1]["a_coverage"], item[0]))[0]
        apply_calibration(dev, prediction, config)
        frozen = {"schema": 2, "dataset": paths["name"], "version": version, "manifest_hash": manifest["hash"],
                  "dev_ids": [u["id"] for u in dev], "dev_truth_hash": digest({u["id"]: answers[u["id"]] for u in dev}),
                  "fingerprints": fingerprints, "config": config, "calibrator_hash": calibrator_hash(config),
                  "holdout_policy": policy, "registry_snapshot": str(Path(snapshot).resolve()) if snapshot else None,
                  "development": metrics(dev, answers, prediction, config=config),
                  "jev_development": jev_development,
                  "scan": [{"config": r["config"] | {"calibrator": None, "owner_calibrator": None, "named_calibrator": None}, "metrics": r["metrics"]} for r in results],
                  "frozen_at": time.time()}
        write(paths["frozen"], frozen)
        print(json.dumps({"status": "frozen", "path": str(paths["frozen"]), "holdout_predictions_inspected": False,
                          "calibration_available": {key: config[key] is not None for key in ("calibrator", "owner_calibrator", "named_calibrator")}}, ensure_ascii=False))
        return frozen
    config = frozen["config"]
    if config.get("use_jev") and jev is None:
        return pending("pending_model", "冻结版本要求 Jev；不能在保留集临时替换为声纹版本")
    holdout = parts["holdout"]
    consumption_ids = sorted({u.get("recording_hash") or u["recording"] for u in holdout})
    # Consumption is journaled before the final look, including interrupted attempts.
    journal_consumption(paths["ledger"], consumption_ids, frozen["version"], digest(frozen), digest(truth))
    baseline = metrics(holdout, answers, predict(holdout, config, baseline=True), config=config)
    final = metrics(holdout, answers, predict(holdout, config), config=config)
    statuses = gate_statuses(final, baseline)
    report = {"schema": 2, "dataset": paths["name"], "version": frozen["version"], "finalized": True,
              "manifest_hash": manifest["hash"], "truth_hash": digest(truth), "frozen_hash": digest(frozen),
              "fingerprints": fingerprints, "calibrator_hash": calibrator_hash(config), "config": config,
              "dataset_files": {"manifest": "manifest.json", "truth": "truth.json", "frozen": "frozen.json"},
              "holdout_count": len(holdout), "holdout_ids": [u["id"] for u in holdout],
              "holdout_recordings": frozen["holdout_policy"]["recordings"], "holdout_consumed": True,
              "holdout_consumption_ids": consumption_ids, "holdout_policy": frozen["holdout_policy"],
              "baseline": baseline, "final": final, "gate_statuses": statuses,
              "split_passed": statuses["display"] == "pass", "strict_a_passed": statuses["strict_a"] == "pass",
              "self_owner_passed": statuses["self_owner"] == "pass", "named_owner_passed": statuses["named_owner"] == "pass",
              "switches": {"display": False, "strict_a": False, "self_owner": False, "named_owner": False},
              "source": manifest.get("source"), "sampling": manifest.get("sampling"),
              "acceptance_scope": manifest.get("acceptance_scope", "unspecified"),
              "limitations": "Conditional independent-Bernoulli limits. Sentences in a meeting may be correlated; a single meeting does not prove cross-meeting stability.",
              "finalized_at": time.time()}
    write(paths["validation"], report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report
