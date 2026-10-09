#!/usr/bin/env python3
"""Where the time goes after someone speaks: p50/p90 per stage from archived `timing.jsonl` files.

The phone writes one JSON line per stage and sentence (final, speaker, split, tag, draft); every numeric
field ending in `_ms` is summarised, `server.*` being the backend's own stage timings. Usage:

    latency_report.py ~/oops-archive/recordings            # every session on the Mac mirror
    latency_report.py some/session/timing.jsonl --json      # one file, machine-readable
"""
import argparse
import json
import math
import os
import sys


def find(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for folder, _, names in os.walk(path):
            for name in sorted(names):
                if name == "timing.jsonl" or name.endswith(".timing.jsonl"):
                    yield os.path.join(folder, name)


def flatten(line):
    for key, value in line.items():
        if key == "server" and isinstance(value, dict):
            for inner, number in value.items():
                yield "server." + inner, number
        else:
            yield key, value


def percentile(values, q):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def reuse_rates(files):
    """Share of watched requests that reused a connection (stage `net`): a new connection costs DNS + TCP + TLS."""
    counts = {}
    for path in files:
        with open(path, encoding="utf-8") as stream:
            for raw in stream:
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if line.get("stage") == "net" and isinstance(line.get("reused"), bool):
                    seen = counts.setdefault(line.get("id", "?"), [0, 0])
                    seen[0] += line["reused"]
                    seen[1] += 1
    return {path: {"reused": a, "calls": n} for path, (a, n) in sorted(counts.items())}


def collect(files):
    table, sessions = {}, 0
    for path in files:
        sessions += 1
        with open(path, encoding="utf-8") as stream:
            for raw in stream:
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                stage = line.get("stage")
                if not isinstance(stage, str):
                    continue
                for field, value in flatten(line):
                    if field.endswith("_ms") and type(value) in (int, float) and math.isfinite(value) and value >= 0:
                        table.setdefault((stage, field), []).append(value)
    return table, sessions


def summarise(table):
    return [{"stage": stage, "field": field, "n": len(values), "p50": percentile(values, .5),
             "p90": percentile(values, .9), "max": max(values)}
            for (stage, field), values in sorted(table.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", help="timing.jsonl files or folders that contain them")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    files = list(find(args.paths))
    table, sessions = collect(files)
    rows = summarise(table)
    reuse = reuse_rates(files)
    if args.json:
        print(json.dumps({"sessions": sessions, "rows": rows, "connection_reuse": reuse}, ensure_ascii=False, indent=1))
    elif not rows:
        print("没有找到耗时记录（需要新版 App 录的会议）", file=sys.stderr)
        return 1
    else:
        print(f"{sessions} 场会议")
        print(f"{'阶段':<9}{'指标':<24}{'次数':>6}{'p50':>9}{'p90':>9}{'最大':>9}  (毫秒)")
        for row in rows:
            print(f"{row['stage']:<9}{row['field']:<24}{row['n']:>6}{row['p50']:>9.0f}{row['p90']:>9.0f}{row['max']:>9.0f}")
        for path, seen in reuse.items():
            print(f"连接复用 {path}: {seen['reused']}/{seen['calls']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
