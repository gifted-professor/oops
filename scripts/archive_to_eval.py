#!/usr/bin/env python3
"""Turn one archived meeting (phone recording uploaded to the server) into inputs for the offline tools.

    archive_to_eval.py ~/oops-archive/recordings/<session> --purpose meeting-development
    archive_to_eval.py ~/oops-archive/recordings/<session> --purpose meeting-holdout

Writes, under the repo root:
  test-media/<id>-16k-mono.wav (+ .json with the purpose)   audio for voiceprint.py split-prepare / split-eval
  evidence/asr-words-archive/<id>/{finals.json,summary.json}  the sentences those tools read
  evidence/<id>/meeting.json                                  sentences to label tasks (task_eval.py --meeting)

A held-out meeting must be chosen before anything is tuned on it: `--purpose` is recorded, and
voiceprint_split refuses a recording whose purpose does not match the set it is used in.
"""
import argparse
import json
import os
import sys
import wave

PURPOSES = ("meeting-development", "meeting-holdout")
SAMPLE_RATE = 16000


def read_jsonl(path):
    with open(path, encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=1)


def write_wav(pcm_path, wav_path):
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    with open(pcm_path, "rb") as source, wave.open(wav_path, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(SAMPLE_RATE)
        for block in iter(lambda: source.read(1 << 20), b""):
            target.writeframes(block)


def convert(session_dir, purpose, root, allow_incomplete=False):
    if purpose not in PURPOSES:
        raise ValueError("purpose 必须是 " + " 或 ".join(PURPOSES))
    session_dir = os.path.abspath(session_dir)
    session = os.path.basename(session_dir.rstrip("/"))
    if not allow_incomplete and not os.path.isfile(os.path.join(session_dir, "complete.json")):
        raise ValueError("这场录音服务器尚未确认完整（没有 complete.json）")
    pcm_path = os.path.join(session_dir, "audio.pcm")
    size = os.path.getsize(pcm_path)
    if size % 2 or size < SAMPLE_RATE * 2:
        raise ValueError("audio.pcm 大小异常")
    duration_ms = size // 32
    finals = []
    for row in read_jsonl(os.path.join(session_dir, "finals.jsonl")):
        if not isinstance(row.get("text"), str) or not row["text"].strip() or not 0 <= row["start_ms"] < row["end_ms"]:
            continue
        if row["end_ms"] > duration_ms + 200:
            raise ValueError(f"句子 {row.get('id')} 超出录音长度；音频和文字对不上")
        finals.append(row)
    if not finals:
        raise ValueError("没有可用的定稿句子")
    name = f"{session}-16k-mono.wav"
    wav_path = os.path.join(root, "test-media", name)
    sidecar = os.path.splitext(wav_path)[0] + ".json"
    if os.path.exists(sidecar):
        with open(sidecar, encoding="utf-8") as stream:
            existing = json.load(stream).get("purpose")
        if existing and existing != purpose:
            raise ValueError(f"{name} 已经登记为 {existing}，不能改成 {purpose}")
    write_wav(pcm_path, wav_path)
    write_json(sidecar, {"purpose": purpose, "session": session, "source": "archive", "duration_ms": duration_ms})
    base = os.path.join(root, "evidence", "asr-words-archive", session)
    write_json(os.path.join(base, "finals.json"), [
        {"text": r["text"], "start_time": r["start_ms"], "end_time": r["end_ms"],
         "words": [{"text": w["text"], "start_time": w["start_ms"], "end_time": w["end_ms"]} for w in r.get("words", [])]}
        for r in finals])
    write_json(os.path.join(base, "summary.json"), {"complete": True, "transport_errors": [], "recording": name,
                                                   "source": "archive", "utterances": len(finals)})
    write_json(os.path.join(root, "evidence", session, "meeting.json"), {
        "session_id": session, "utterances": [
            {"id": r["id"], "text": r["text"], "start_ms": r["start_ms"], "end_ms": r["end_ms"], "speaker": None}
            for r in finals]})
    return {"wav": wav_path, "recording": name, "utterances": len(finals), "duration_ms": duration_ms,
            "finals": os.path.join(base, "finals.json"), "meeting": os.path.join(root, "evidence", session, "meeting.json")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_dir")
    parser.add_argument("--purpose", required=True, choices=PURPOSES)
    parser.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(convert(args.session_dir, args.purpose, args.root, args.allow_incomplete), ensure_ascii=False, indent=1))
    except (ValueError, OSError) as error:
        print("失败：" + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
