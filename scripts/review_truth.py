#!/usr/bin/env python3
"""Turn a meeting's in-app review (会后核对) into a truth file for task_eval.py, for the development set only.

    review_truth.py /Volumes/GPFS/Users/a1234/oops-archive/meetings/<session>.json
    → evidence/<session>/review-truth.json

Two limits, both written into the file:
- Not blind: the review screen shows the model's guess, so this must never become a held-out acceptance set.
- Only the model's candidates were asked about: a task the model never tagged is missing, so recall measured
  against this file is too high. Use it for precision and owner accuracy; blind labels (label_meeting.py) for recall.
"""
import argparse
import json
import os
import sys

from label_meeting import derive_truth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def review_truth(meeting):
    """Reviews in label_meeting's label shape, then its truth format plus the limits and the decisions."""
    utterances = meeting["utterances"]
    reviews = meeting.get("reviews", {})
    tags = {u["id"]: u.get("tag") for u in utterances}
    labels, decisions = {}, {"confirmed": [], "rejected": []}
    for uid, review in reviews.items():
        if uid not in tags:
            continue
        label = {}
        if review.get("speaker"):
            label["speaker"] = review["speaker"]
        verdict = review.get("verdict")
        if tags[uid] == "commitment" and verdict:
            label["task"] = "task" if verdict == "confirmed" else "not_task"
            if verdict == "confirmed":
                label["owner"] = review.get("owner") or "unknown"
        elif tags[uid] == "decision" and verdict:
            decisions[verdict].append(uid)
        if label:
            labels[uid] = label
    truth = derive_truth(meeting["session_id"], utterances, labels)
    return dict(truth, blind=False, scope="model_candidates_only", decisions=decisions)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("meeting", help="a meeting file from the server's meetings/ directory (or its nightly mirror)")
    parser.add_argument("--out", help="default: evidence/<session>/review-truth.json")
    args = parser.parse_args()
    with open(args.meeting, encoding="utf-8") as stream:
        meeting = json.load(stream)
    truth = review_truth(meeting)
    out = args.out or os.path.join(ROOT, "evidence", meeting["session_id"], "review-truth.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as stream:
        json.dump(truth, stream, ensure_ascii=False, indent=1)
    print(f"{out}: {len(truth['tasks'])} 条任务、{len(truth['non_tasks'])} 条非任务、{len(truth['speakers'])} 句说话人；"
          "非盲标、只含模型候选，只能进开发集", file=sys.stderr)


if __name__ == "__main__":
    main()
