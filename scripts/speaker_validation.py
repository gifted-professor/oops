"""Offline calibration and fixed-look speaker acceptance. No live switches are enabled here.

Binomial limits are conditional on the stated independent Bernoulli sampling model;
sentence counts do not establish independence across meetings. Bootstrap is descriptive.
"""
import hashlib
import json
import math
from pathlib import Path
import random

SEGMENT_EVENT = "segment_dominant_90pct"
OWNER_EVENT = "commitment_span_correct"
NAMED_EVENT = "named_action_binding_correct"
FEATURES = ("top_score", "margin", "duration_seconds", "overlap", "jev_used",
            "selected_score", "selected_rank", "prior_agreement", "near_overlap")
OWNER_FEATURES = tuple(name for name in FEATURES if name != "near_overlap") + ("span_fraction", "span_chars")
NAMED_FEATURES = ("span_chars", "explicit_named", "single_relation")


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode()).hexdigest()


def _binomial_cdf(k, n, p):
    """Log-sum-exp avoids factorial overflow and cancellation near rare-event tails."""
    if p <= 0:
        return 1.0
    if p >= 1:
        return float(k >= n)
    logp, logq = math.log(p), math.log1p(-p)
    terms = [math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1)
             + i * logp + (n - i) * logq for i in range(k + 1)]
    largest = max(terms)
    return min(1.0, math.exp(largest) * math.fsum(math.exp(x - largest) for x in terms))


def cp_upper(errors, total, alpha=.05):
    """One-sided Clopper-Pearson upper error-rate bound, including zero observations."""
    if (isinstance(errors, bool) or isinstance(total, bool) or not isinstance(errors, int)
            or not isinstance(total, int) or not 0 <= errors <= total or not 0 < alpha < 1):
        raise ValueError("Invalid binomial counts or alpha")
    if total == 0 or errors == total:
        return 1.0
    if errors == 0:
        return -math.expm1(math.log(alpha) / total)
    lo, hi = errors / total, 1.0
    for _ in range(70):
        middle = (lo + hi) / 2
        if _binomial_cdf(errors, total, middle) > alpha:
            lo = middle
        else:
            hi = middle
    return (lo + hi) / 2


def cp_lower(successes, total, alpha=.05):
    return 1.0 - cp_upper(total - successes, total, alpha)


def evidence_status(errors, total, target, alpha=.05):
    """Too few zero-error trials cannot pass; an observed excessive rate is a failure."""
    bound = cp_upper(errors, total, alpha)
    if total == 0:
        status = "insufficient_evidence"
    elif errors / total > target:
        status = "fail"
    elif bound <= target:
        status = "pass"
    else:
        status = "insufficient_evidence"
    return {"status": status, "errors": errors, "trials": total, "upper": bound,
            "target": target, "alpha": alpha, "method": "Clopper-Pearson-one-sided"}


def fit_logistic(rows, labels, features=FEATURES, event=SEGMENT_EVENT, l2=1.0):
    """Fit on development labels only. One-class/empty data yields no calibration."""
    if len(rows) != len(labels) or any(label not in (0, 1) for label in labels):
        raise ValueError("Invalid calibration labels")
    if len(rows) < 4 or len(set(labels)) != 2:
        return None
    import numpy as np
    x = np.array([[float(row.get(name, 0)) for name in features] for row in rows])
    y = np.array(labels, dtype=float)
    if not np.isfinite(x).all():
        raise ValueError("Non-finite calibration feature")
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1
    design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    beta = np.zeros(design.shape[1])
    penalty = np.eye(len(beta)) * l2
    penalty[0, 0] = 1e-6
    for _ in range(80):
        eta = np.clip(design @ beta, -35, 35)
        prob = 1 / (1 + np.exp(-eta))
        weight = np.maximum(prob * (1 - prob), 1e-8)
        hessian = design.T @ (design * weight[:, None]) + penalty
        gradient = design.T @ (prob - y) + penalty @ beta
        delta = np.linalg.solve(hessian, gradient)
        beta -= delta
        if np.max(np.abs(delta)) < 1e-7:
            break
    return {"schema": 1, "event": event, "features": list(features), "mean": mean.tolist(),
            "scale": scale.tolist(), "weights": beta[1:].tolist(), "intercept": float(beta[0]),
            "development_samples": len(labels), "development_errors": len(labels) - sum(labels),
            "l2": l2}


def calibrated_probability(calibrator, row, event=None):
    if not calibrator:
        return None
    try:
        if calibrator.get("schema") != 1 or event and calibrator.get("event") != event:
            return None
        keys = calibrator["features"]
        arrays = [calibrator[k] for k in ("mean", "scale", "weights")]
        if not keys or any(len(v) != len(keys) for v in arrays):
            return None
        value = float(calibrator["intercept"])
        for name, mean, scale, weight in zip(keys, *arrays):
            if float(scale) <= 0:
                return None
            value += (float(row.get(name, 0)) - float(mean)) / float(scale) * float(weight)
        if not math.isfinite(value):
            return None
        return 1 / (1 + math.exp(-max(-60, min(60, value))))
    except (ValueError, TypeError, KeyError, OverflowError):
        return None


def calibration_error(samples, bins=10):
    """Each item is (predicted probability, binary event), never one item per character."""
    usable = [(p, y) for p, y in samples if p is not None and 0 <= p <= 1]
    if not usable:
        return None
    groups = [[] for _ in range(bins)]
    for p, y in usable:
        groups[min(bins - 1, int(p * bins))].append((p, y))
    return sum(abs(sum(p for p, _ in group) - sum(y for _, y in group))
               for group in groups if group) / len(usable)


def descriptive_bootstrap(sentence_counts, seed=7, repeats=1000):
    """Counts are (wrong claimed characters, claimed characters), sampled by sentence."""
    if not sentence_counts or not any(total for _, total in sentence_counts):
        return None
    rng, values = random.Random(seed), []
    for _ in range(repeats):
        picked = [rng.choice(sentence_counts) for _ in sentence_counts]
        denominator = sum(total for _, total in picked)
        if denominator:
            values.append(sum(wrong for wrong, _ in picked) / denominator)
    values.sort()
    return {"lower": values[int(.025 * (len(values) - 1))],
            "upper": values[int(.975 * (len(values) - 1))],
            "method": "sentence-cluster-bootstrap", "descriptive_only": True,
            "zero_error_degenerate": all(wrong == 0 for wrong, _ in sentence_counts)}


def gate_statuses(final, baseline):
    sufficient_display = (final.get("chars", 0) > 0 and final.get("change_count", 0) > 0
                          and final.get("short_count", 0) > 0)
    display_pass = sufficient_display and (
        final["char_accuracy"] >= baseline["char_accuracy"] + .1 - 1e-12
        and final["wrong_claim_rate"] <= min(.05, baseline["wrong_claim_rate"]) + 1e-12
        and final["change_recall"] >= .7 and final["short_recall"] >= .5
        and final["short_wrong_rate"] <= .1)
    display = "pass" if display_pass else "fail" if sufficient_display else "insufficient_evidence"
    segment = evidence_status(final.get("a_segment_wrong", 0), final.get("a_segment_count", 0), .02)
    sentence = evidence_status(final.get("a_sentence_wrong", 0), final.get("a_sentence_count", 0), .02)
    def combined(*statuses):
        return "fail" if "fail" in statuses else "pass" if all(s == "pass" for s in statuses) else "insufficient_evidence"
    strict = combined(display, segment["status"], sentence["status"])
    owners = {}
    for kind in ("self", "named"):
        bound = evidence_status(final.get(kind + "_wrong", 0), final.get(kind + "_claimed", 0), .05)
        coverage = "pass"
        if kind == "self":
            coverage = ("pass" if final.get("self_count", 0) > 0 and final.get("self_coverage", 0) >= .6
                        else "fail" if final.get("self_count", 0) > 0 else "insufficient_evidence")
        owners[kind] = bound | {"status": combined(strict, bound["status"], coverage),
                               "coverage_status": coverage}
    return {"display": display, "strict_a": strict, "self_owner": owners["self"]["status"],
            "named_owner": owners["named"]["status"], "segment_bound": segment,
            "sentence_bound": sentence, "self_bound": owners["self"], "named_bound": owners["named"]}


def frozen_holdout_ready(manifest, answers, policy):
    """Only cohort IDs/review completeness/annotated counts are inspected before final look."""
    holdout = [case for case in manifest["cases"] if case["partition"] == "holdout"]
    recordings = sorted({case["recording"] for case in holdout})
    expected = sorted(policy["recordings"])
    if recordings != expected:
        return False, "冻结场次清单未齐或发生变化"
    if len(holdout) < policy["minimum_cases"]:
        return False, "冻结的最终保留样本目标尚未达到"
    if any(not answers.get(case["id"], {}).get("reviewed") for case in holdout):
        return False, "待人工完成最终保留集标注"
    for kind in ("self", "named"):
        count = 0
        for case in holdout:
            answer = answers[case["id"]]
            if "commitments" in answer:
                count += sum(span.get("kind") == kind for span in answer["commitments"])
            elif kind == "self":
                count += bool(answer.get("self_present"))
        if count < policy.get("minimum_" + kind, 0):
            return False, "冻结的负责人样本目标尚未达到"
    return True, None


def consume_holdout(ledger, recordings, version, frozen_hash, truth_hash):
    """A consumed recording can only reproduce its original frozen version and truth."""
    entries = ledger.setdefault("recordings", {})
    proposed = {"version": version, "frozen_hash": frozen_hash, "truth_hash": truth_hash}
    for recording in recordings:
        if recording in entries and any(entries[recording].get(k) != v for k, v in proposed.items()):
            raise ValueError("保留场次已消费；变更版本或真值只能作回归，需新独立场次验收")
    repeated = all(recording in entries for recording in recordings)
    for recording in recordings:
        entries[recording] = proposed | {"consumed": True}
    return repeated


def calibrator_hash(config):
    return digest({key: config.get(key) for key in ("calibrator", "owner_calibrator", "named_calibrator")})


def disabled_validation(reason="Independent real-chain evidence is pending", dataset=None, version=None):
    """Deployment bootstrap metadata; it makes no acceptance claim and enables nothing."""
    return {"schema": 2, "finalized": False, "status": "pending_human", "reason": reason,
            "dataset": dataset, "version": version,
            "switches": {"display": False, "strict_a": False, "self_owner": False, "named_owner": False},
            "config": {"calibrator": None, "owner_calibrator": None, "named_calibrator": None,
                       "strict_a_enabled": False, "self_owner_enabled": False, "named_owner_enabled": False}}


def _approved_configuration(report, statuses, include_disabled=False):
    config = report["config"]
    switches = report.get("switches", {})
    requested = any(switches.get(key) is True for key in ("display", "strict_a", "self_owner", "named_owner"))
    if requested and report.get("acceptance_scope") != "actual-chain":
        return None
    if requested and config.get("use_jev"):
        contract = config.get("jev_contract") or {}
        if (contract.get("pinned") is not True or not contract.get("requested_model")
                or len(contract.get("observed_engines", [])) != 1 or not contract.get("url")):
            return None
    display = switches.get("display") is True and statuses["display"] == "pass"
    strict = display and switches.get("strict_a") is True and statuses["strict_a"] == "pass"
    self_enabled = strict and switches.get("self_owner") is True and statuses["self_owner"] == "pass"
    named_enabled = strict and switches.get("named_owner") is True and statuses["named_owner"] == "pass"
    for enabled, key, event in ((strict, "calibrator", SEGMENT_EVENT),
                                (self_enabled, "owner_calibrator", OWNER_EVENT),
                                (named_enabled, "named_calibrator", NAMED_EVENT)):
        model = config.get(key)
        if enabled and (not model or calibrated_probability(model, {f: 0.0 for f in model.get("features", [])}, event) is None):
            return None
    if not display and not include_disabled:
        return None
    approved_config = config | {"strict_a_enabled": strict, "self_owner_enabled": self_enabled,
                                "named_owner_enabled": named_enabled}
    return {"verified": True, "split_config": approved_config, "split_display_enabled": display,
            "strict_a_enabled": strict, "self_owner_enabled": self_enabled, "named_owner_enabled": named_enabled}


def verify_compact_validation(receipt, speakers=None, signature_verified=False, include_disabled=False):
    """A pinned-key signature authenticates the offline receipt, never a bundle-supplied key."""
    try:
        from local_backend import split_fingerprints
        if (signature_verified is not True or receipt.get("schema") != 2
                or receipt.get("kind") != "signed-deployment-receipt"
                or receipt.get("finalized") is not True or receipt.get("holdout_consumed") is not True
                or receipt.get("fingerprints") != split_fingerprints(speakers)
                or receipt.get("calibrator_hash") != calibrator_hash(receipt["config"])):
            return None
        ids = receipt["holdout_ids"]
        if (not ids or len(set(ids)) != len(ids) or receipt["holdout_count"] != len(ids)
                or not receipt.get("holdout_recordings")
                or sorted(receipt["holdout_recordings"]) != sorted(receipt["holdout_policy"]["recordings"])
                or receipt["holdout_count"] < receipt["holdout_policy"]["minimum_cases"]
                or receipt["final"].get("a_sentence_count", 0) > len(ids)):
            return None
        statuses = gate_statuses(receipt["final"], receipt["baseline"])
        if statuses != receipt["gate_statuses"]:
            return None
        return _approved_configuration(receipt, statuses, include_disabled)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def verify_validation(report, path, speakers=None, include_disabled=False):
    """Fail closed on stale/partial/tampered schema2 evidence; old reports never enable A."""
    try:
        from local_backend import split_fingerprints
        path = Path(path).resolve()
        if report.get("kind") == "signed-deployment-receipt":
            from speaker_acceptance import verify_receipt_signature
            if not verify_receipt_signature(path) or json.loads(path.read_text()) != report:
                return None
            calibrators_path, frozen_path = path.parent / "calibrators.json", path.parent / "frozen.json"
            if (hashlib.sha256(calibrators_path.read_bytes()).hexdigest() != report["calibrators_file_hash"]
                    or hashlib.sha256(frozen_path.read_bytes()).hexdigest() != report["compact_frozen_hash"]):
                return None
            expected_calibrators = {key: report["config"].get(key) for key in ("calibrator", "owner_calibrator", "named_calibrator")}
            if json.loads(calibrators_path.read_text()) != expected_calibrators:
                return None
            compact_frozen = json.loads(frozen_path.read_text())
            if (compact_frozen.get("config") != report["config"]
                    or compact_frozen.get("version") != report["version"]
                    or compact_frozen.get("fingerprints") != report["fingerprints"]
                    or compact_frozen.get("holdout_policy") != report["holdout_policy"]
                    or compact_frozen.get("source_frozen_hash") != report["frozen_hash"]):
                return None
            return verify_compact_validation(report, speakers, signature_verified=True, include_disabled=include_disabled)
        if report.get("schema") != 2 or report.get("finalized") is not True:
            return None
        if report["fingerprints"] != split_fingerprints(speakers):
            return None
        config = report["config"]
        if report.get("calibrator_hash") != calibrator_hash(config):
            return None
        files = report["dataset_files"]
        loaded = {}
        for key in ("manifest", "truth", "frozen"):
            relative = Path(files[key])
            if relative.is_absolute() or ".." in relative.parts:
                return None
            loaded[key] = json.loads((path.parent / relative).read_text())
        if (loaded["manifest"]["hash"] != report["manifest_hash"]
                or digest(loaded["truth"]) != report["truth_hash"]
                or digest(loaded["frozen"]) != report["frozen_hash"]
                or loaded["frozen"]["config"] != config):
            return None
        # Never accept only a copied manifest hash: recompute its complete content.
        manifest = dict(loaded["manifest"])
        manifest.pop("hash", None)
        if digest(manifest) != report["manifest_hash"]:
            return None
        from voiceprint_split import validated_truth, validate_partitions
        validate_partitions(loaded["manifest"])
        validated_truth(loaded["truth"], loaded["manifest"])
        ready, _ = frozen_holdout_ready(loaded["manifest"], loaded["truth"]["answers"],
                                       loaded["frozen"]["holdout_policy"])
        if not ready or not report.get("holdout_consumed"):
            return None
        holdout = [c for c in loaded["manifest"]["cases"] if c["partition"] == "holdout"]
        if (report.get("holdout_count") != len(holdout)
                or report.get("holdout_ids") != [c["id"] for c in holdout]
                or report.get("holdout_policy") != loaded["frozen"]["holdout_policy"]
                or report.get("dataset") != loaded["manifest"].get("dataset")
                or report.get("version") != loaded["frozen"].get("version")
                or report["final"].get("a_sentence_count", 0) > len(holdout)
                or report["final"].get("a_segment_count", 0) > sum(len(c["text"]) for c in holdout)):
            return None
        statuses = gate_statuses(report["final"], report["baseline"])
        if report["gate_statuses"] != statuses:
            return None
        return _approved_configuration(report, statuses, include_disabled)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        return None
