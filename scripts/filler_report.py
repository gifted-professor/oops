#!/usr/bin/env python3
"""Review which archived sentences the live transcript hides or rewrites, and which short ones it still shows.

The phone hides sentences made only of interjections (TextClean.FILLER_CHARS) and joins a mobile number read
out in groups. The raw text is archived untouched, so this review can be repeated and the lists extended:

    filler_report.py ~/oops-archive/recordings            # every archived session
    filler_report.py some/session/finals.jsonl --top 30
"""
import argparse
import collections
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KOTLIN = os.path.join(ROOT, "android/app/src/main/java/com/oops/app/core/TextClean.kt")
PUNCT = "，。！？、…,.!?;；:：\\s"


def filler_chars(path=KOTLIN):
    with open(path, encoding="utf-8") as stream:
        match = re.search(r'FILLER_CHARS\s*=\s*"([^"]+)"', stream.read())
    if not match:
        raise ValueError("TextClean.kt 里找不到 FILLER_CHARS")
    return match.group(1)


def patterns(chars):
    return (re.compile(f"[{chars}{PUNCT}]*"),
            re.compile(f"\\d{{2,}}(?:[{chars}{PUNCT}]+\\d{{2,}})+"),
            re.compile(r"1[3-9]\d{9}"))


def find(paths):
    for path in paths:
        if os.path.isfile(path):
            yield path
            continue
        for folder, _, names in os.walk(path):
            for name in sorted(names):
                if name == "finals.jsonl" or name.endswith(".finals.jsonl"):
                    yield os.path.join(folder, name)


def sentences(path):
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("text"), str):
                yield row["text"], max(0, (row.get("end_ms", 0) - row.get("start_ms", 0)) / 1000)


def review(files, chars, short=4):
    filler_only, digit_run, mobile = patterns(chars)
    total = hidden = joined = 0
    seconds = hidden_seconds = 0.0
    short_shown = collections.Counter()
    hidden_forms = collections.Counter()
    for path in files:
        for text, duration in sentences(path):
            total += 1
            seconds += duration
            if filler_only.fullmatch(text):
                hidden += 1
                hidden_seconds += duration
                hidden_forms[text.strip()] += 1
                continue
            if any(mobile.fullmatch("".join(c for c in m.group(0) if c.isdigit())) for m in digit_run.finditer(text)):
                joined += 1
            core = re.sub(f"[{PUNCT}]", "", text)
            if len(core) <= short:
                short_shown[core] += 1
    return {"sentences": total, "seconds": round(seconds, 1), "hidden": hidden, "hidden_seconds": round(hidden_seconds, 1),
            "phone_numbers_joined": joined, "hidden_forms": hidden_forms.most_common(),
            "short_still_shown": short_shown.most_common()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    files = list(find(args.paths))
    if not files:
        print("没有找到 finals.jsonl", file=sys.stderr)
        return 1
    chars = filler_chars()
    report = review(files, chars)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    share = report["hidden"] / report["sentences"] if report["sentences"] else 0
    print(f"{len(files)} 场，{report['sentences']} 句，{report['seconds']} 秒；语气词字符：{chars}")
    print(f"隐藏 {report['hidden']} 句（{share:.0%}，{report['hidden_seconds']} 秒）；拼成一个手机号的句子 {report['phone_numbers_joined']} 句")
    print("被隐藏的写法：", "、".join(f"{t}×{n}" for t, n in report["hidden_forms"][:args.top]) or "无")
    print("仍然显示的短句（≤4 字，看有没有该加进语气词的）：",
          "、".join(f"{t}×{n}" for t, n in report["short_still_shown"][:args.top]) or "无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
