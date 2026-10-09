#!/usr/bin/env python3
"""One line per stored meeting, real ones apart from test and smoke records, for reviewing the team database.

    meetings_overview.py /Volumes/GPFS/Users/a1234/oops-archive          # the nightly Mac mirror (meetings/ + recordings/)
    meetings_overview.py <state-dir> --all                               # include test records in the table

A meeting counts as real when its id is a random UUID (the app makes those; smoke and deploy checks use
readable ids), it has at least 5 sentences, and it is not a deleted tombstone.
"""
import argparse
import collections
import json
import os
import re
import sys

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def load(state):
    folder = os.path.join(state, "meetings")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as stream:
            try:
                yield json.load(stream)
            except json.JSONDecodeError:
                continue


def summarise(meeting, state):
    sid = meeting.get("session_id", "")
    utterances = [u for u in meeting.get("utterances", []) if isinstance(u, dict)]
    spans = [u.get("end_ms", 0) for u in utterances]
    recording = os.path.join(state, "recordings", sid, "complete.json")
    named = sum(1 for u in utterances if u.get("speaker"))
    return {"session_id": sid, "started_at": meeting.get("started_at"), "utterances": len(utterances),
            "minutes": round(max(spans, default=0) / 60000, 1),
            "named_share": round(named / len(utterances), 2) if utterances else 0.0,
            "tags": dict(collections.Counter(u.get("tag") or "none" for u in utterances)),
            "speakers": dict(collections.Counter(u.get("speaker") or "未识别" for u in utterances)),
            "has_recording": os.path.isfile(recording),
            "real": bool(UUID.fullmatch(sid)) and len(utterances) >= 5}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("state_dir")
    parser.add_argument("--all", action="store_true", help="also list test and smoke records")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not os.path.isdir(os.path.join(args.state_dir, "meetings")):
        print("找不到 meetings 目录", file=sys.stderr)
        return 1
    rows = [summarise(m, args.state_dir) for m in load(args.state_dir)]
    real = [r for r in rows if r["real"]]
    shown = rows if args.all else real
    if args.json:
        print(json.dumps({"total": len(rows), "real": len(real), "meetings": shown}, ensure_ascii=False, indent=1))
        return 0
    print(f"共 {len(rows)} 条记录，其中真实会议 {len(real)} 场，测试/冒烟 {len(rows) - len(real)} 条")
    print(f"{'会议':<10}{'开始':<17}{'句数':>5}{'分钟':>6}{'有名':>6}  录音  标签")
    for r in shown:
        tags = " ".join(f"{k}:{v}" for k, v in sorted(r["tags"].items(), key=lambda x: -x[1])[:4])
        mark = "" if r["real"] else " (测试)"
        print(f"{r['session_id'][:8]:<10}{(r['started_at'] or '')[:16]:<17}{r['utterances']:>5}{r['minutes']:>6}"
              f"{r['named_share']:>6.0%}  {'有' if r['has_recording'] else '无'}    {tags}{mark}")
    if real:
        total = sum(r["utterances"] for r in real)
        named = sum(r["named_share"] * r["utterances"] for r in real)
        print(f"真实会议合计：{total} 句，有名字 {named / total:.0%}，带录音 {sum(r['has_recording'] for r in real)} 场")
    return 0


if __name__ == "__main__":
    sys.exit(main())
