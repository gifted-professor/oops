#!/usr/bin/env python3
"""How well does the live pipeline find tasks in a labelled meeting? Recall, precision and run-to-run stability.

    task_eval.py --meeting evidence/task-1008/meeting.json --truth evidence/task-1008/truth.json \\
                 --method live --runs 3 --out evidence/task-1008/eval-live.json

Methods (all call the real services, so they need .jev.env / .deepseek.env):
  live             Backend.tag exactly as the phone triggers it: sentence tag, then per-clause check.
                   Jev answers HTTP 451 outside the HK server's region, so run this one on the server.
  resolver         DeepSeek context resolver with the live window (12 lines before, none after)
  resolver-legacy  the window used for the first measurement (8 before, 1 after), kept to compare with it

`truth.json` lists `tasks` (utterance ids), `non_tasks` (hard negatives), and `duplicates` (groups of
utterances that state the same task). A group is one task: finding any member finds it, and flagging
several members is not a false alarm, but it is reported as `duplicates_flagged` (cross-sentence dedup
is still to do).
"""
import argparse
import json
import os
import sys
import types
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def score(truth, flagged):
    """Utterance-level and task-level counts for one run; `flagged` is a set of utterance ids."""
    flagged = set(flagged)
    groups = [list(g) for g in truth.get("duplicates", [])]
    grouped = {u for g in groups for u in g}
    positives = {t["utterance"] for t in truth["tasks"]} | grouped
    hard_negatives = {n["utterance"] for n in truth.get("non_tasks", [])}
    tp, fp, fn = flagged & positives, flagged - positives, positives - flagged
    units = [[u] for u in sorted(positives - grouped)] + groups
    found_units = [u for u in units if flagged & set(u)]
    recall, precision = len(tp) / len(positives) if positives else 1.0, len(tp) / len(flagged) if flagged else 1.0
    return {"positives": len(positives), "tp": len(tp), "fp": len(fp), "fn": len(fn),
            "false_positives": sorted(fp), "missed": sorted(fn),
            "recall": round(recall, 3), "precision": round(precision, 3),
            "tasks": len(units), "tasks_found": len(found_units),
            "hard_negatives_flagged": sorted(flagged & hard_negatives),
            "duplicates_flagged": sum(1 for g in groups if len(flagged & set(g)) > 1)}


def stability(truth, runs):
    """Per utterance: in how many runs it was flagged. Anything strictly between 0 and N is an unstable call."""
    count = {}
    for flagged in runs:
        for u in flagged:
            count[u] = count.get(u, 0) + 1
    wrong_or_unstable = {u: n for u, n in count.items() if 0 < n < len(runs)}
    return {"runs": len(runs), "flagged_in_every_run": sorted(u for u, n in count.items() if n == len(runs)),
            "unstable": dict(sorted(wrong_or_unstable.items()))}


def live_flags(utterances, jev, workers=6):
    from local_backend import Backend, TagCache
    backend = Backend.__new__(Backend)
    backend.jev, backend.tags = jev, TagCache()

    def one(i):
        u = utterances[i]
        context = [x["text"][-120:] for x in utterances[max(0, i - 4):i]]
        result = backend.tag({"session_id": "eval", "utterance_id": u["id"], "text": u["text"], "context": context,
                              "start_ms": u.get("start_ms", 0), "end_ms": u.get("end_ms", 0)})
        # Backend.tag quietly falls back to keyword rules when Jev fails; scoring that would mislabel it as Jev.
        if str(result.get("engine", "")).startswith("rules"):
            raise RuntimeError("Jev 不可用，结果退回了关键词规则；请在能访问 Jev 的主机（香港服务器）上运行 live 方法")
        return u["id"], result["tag"] == "commitment"
    with ThreadPoolExecutor(workers) as pool:
        return {uid for uid, flagged in pool.map(one, range(len(utterances))) if flagged}


def resolver_flags(utterances, model, window, after, workers=6):
    from task_resolver import resolve_all
    lines = [{"id": u["id"], "text": u["text"]} for u in utterances]

    def one(u):
        return u["id"], bool(resolve_all(model, lines, u["id"], window=window, after=after, timeout=30))
    with ThreadPoolExecutor(workers) as pool:
        return {uid for uid, flagged in pool.map(one, utterances) if flagged}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--meeting", required=True)
    parser.add_argument("--truth", required=True)
    parser.add_argument("--method", choices=("live", "resolver", "resolver-legacy"), default="live")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    utterances = json.load(open(args.meeting, encoding="utf-8"))["utterances"]
    truth = json.load(open(args.truth, encoding="utf-8"))
    if truth.get("scope") == "model_candidates_only":
        print("注意：这份真值来自会后核对，只问过模型标出的句子，召回会偏高；看精确率和负责人即可", file=sys.stderr)
    if args.method == "live":
        import local_backend
        jev = local_backend.load_jev(types.SimpleNamespace(jev_env=os.path.join(ROOT, ".jev.env"), jev_model="jev-latest", no_jev=False))
        if jev is None:
            sys.exit("没有可用的 Jev 凭据（.jev.env）")
        run = lambda: live_flags(utterances, jev)
    else:
        from task_resolver import load_deepseek
        model = load_deepseek(ROOT)
        if model is None:
            sys.exit("没有可用的 DeepSeek 凭据（.deepseek.env）")
        window, after = (12, 0) if args.method == "resolver" else (8, 1)
        run = lambda: resolver_flags(utterances, model, window, after)
    flagged_runs, results = [], []
    for _ in range(args.runs):
        flagged = run()
        flagged_runs.append(flagged)
        results.append(score(truth, flagged))
        r = results[-1]
        print(f"TP {r['tp']}  FP {r['fp']} {r['false_positives']}  FN {r['fn']} {r['missed']}  "
              f"召回 {r['recall']}  精确 {r['precision']}  任务 {r['tasks_found']}/{r['tasks']}", flush=True)
    report = {"meeting": truth.get("meeting"), "method": args.method, "runs": results,
              "stability": stability(truth, flagged_runs) if args.runs > 1 else None}
    if report["stability"]:
        print("不稳定的句子（各次结果不一致）：", report["stability"]["unstable"] or "无")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
