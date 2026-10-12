#!/usr/bin/env python3
"""Synthetic checks of fixed-look safety and independent calibration, not device acceptance."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import voiceprint_split as split
from speaker_validation import (FEATURES, OWNER_FEATURES, OWNER_EVENT, SEGMENT_EVENT, NAMED_EVENT,
    cp_upper, cp_lower, evidence_status, fit_logistic, calibrated_probability,
    descriptive_bootstrap, gate_statuses, frozen_holdout_ready, consume_holdout,
    verify_validation, verify_compact_validation, calibrator_hash, disabled_validation)


def constant_model(event, features=FEATURES, probability=.999):
    return {"schema": 1, "event": event, "features": list(features), "mean": [0.0] * len(features),
            "scale": [1.0] * len(features), "weights": [0.0] * len(features),
            "intercept": math.log(probability / (1 - probability))}


def case(id_="u1", text="甲乙", partition="holdout", recording="meeting.wav"):
    return {"id": id_, "text": text, "partition": partition, "recording": recording,
            "start_ms": 0, "end_ms": 2000, "words": [], "boundaries": [{"char": 1, "time_ms": 1000}],
            "context": [], "approx": False, "clip": "clips/" + id_ + ".wav"}


def truth(owner="洪玉麟", cuts=None, segments=None):
    return {"cuts": cuts or [], "segments": segments or [{"speakers": [owner]}],
            "commitments": [], "reviewed": True}


def prediction(text, owner="洪玉麟", tier="A", start=0, end=2000):
    return {"text": text, "speaker": owner, "tier": tier, "confidence": .999,
            "start_ms": start, "end_ms": end, "approx": False, "overlap": False,
            "source": "voiceprint", "candidates": [{"name": owner, "score": .8}],
            "_features": {name: 0.0 for name in FEATURES}}


class ExactLimitTest(unittest.TestCase):
    def test_required_sample_counts_are_exact_at_both_sides_of_boundary(self):
        for target, errors, required in ((.02, 0, 149), (.02, 1, 236), (.05, 0, 59), (.05, 1, 93)):
            with self.subTest(target=target, errors=errors):
                self.assertGreater(cp_upper(errors, required - 1), target)
                self.assertLessEqual(cp_upper(errors, required), target)
        self.assertAlmostEqual(.139108340668265, cp_upper(0, 20), places=13)

    def test_accuracy_lower_limit_uses_failure_complement(self):
        self.assertGreaterEqual(cp_lower(59, 59), .95)
        self.assertGreaterEqual(cp_lower(92, 93), .95)
        self.assertLess(cp_lower(91, 92), .95)
        self.assertEqual(0, cp_lower(0, 0))
        self.assertEqual(0, cp_lower(0, 10))

    def test_large_counts_do_not_overflow_and_invalid_counts_fail(self):
        self.assertLess(cp_upper(1, 100000), .00005)
        self.assertEqual(1, cp_upper(10, 10))
        self.assertEqual("insufficient_evidence", evidence_status(0, 20, .02)["status"])
        self.assertEqual("fail", evidence_status(1, 10, .02)["status"])
        for errors, total in ((1, 0), (-1, 2), (False, 2), (0, 2.0)):
            with self.assertRaises(ValueError):
                cp_upper(errors, total)

    def test_zero_error_bootstrap_is_never_a_gate(self):
        descriptive = descriptive_bootstrap([(0, 100)] * 20)
        self.assertEqual(0, descriptive["upper"])
        self.assertTrue(descriptive["descriptive_only"])
        self.assertTrue(descriptive["zero_error_degenerate"])
        self.assertGreater(cp_upper(0, 20), .02)


class CalibrationTest(unittest.TestCase):
    def test_mixed_development_data_fit_and_wrong_event_is_rejected(self):
        rows = [{"score": i / 10} for i in range(20)]
        model = fit_logistic(rows, [int(i >= 10) for i in range(20)], ("score",), SEGMENT_EVENT)
        self.assertLess(calibrated_probability(model, {"score": .1}, SEGMENT_EVENT), .5)
        self.assertGreater(calibrated_probability(model, {"score": 1.8}, SEGMENT_EVENT), .5)
        self.assertIsNone(calibrated_probability(model, {"score": 1.8}, OWNER_EVENT))
        self.assertIsNone(fit_logistic(rows, [1] * 20, ("score",), SEGMENT_EVENT))
        self.assertIsNone(fit_logistic([], [], ("score",), SEGMENT_EVENT))

    def test_segment_dominance_can_succeed_while_short_commitment_owner_fails(self):
        text = "甲" * 27 + "我来做"
        u = case(text=text)
        u["boundaries"] = [{"char": 27, "time_ms": 1700}]
        answer = truth(cuts=[27], segments=[{"speakers": ["洪玉麟"]}, {"speakers": ["林春源"]}])
        answer["commitments"] = [{"start": 27, "end": 30, "kind": "self", "owner": "林春源",
                                   "action_start": 27, "action_end": 30}]
        pred = [prediction(text)]
        self.assertEqual(1, split.segment_records(u, answer, pred)[0]["correct"])
        config = {"owner_calibrator": constant_model(OWNER_EVENT, OWNER_FEATURES), "thresholds": {"owner_a": .95}}
        record = split.commitment_records(u, answer, pred, config)[0]
        self.assertTrue(record["eligible"])
        self.assertEqual(0, record["correct"])
        result = split.metrics([u], {"u1": answer}, {"u1": pred}, config=config)
        self.assertEqual(0, result["a_segment_wrong"])
        self.assertEqual(1, result["self_wrong"])

    def test_action_binding_is_required_even_when_speaker_matches(self):
        u = case(text="我来做方案")
        answer = truth()
        answer["commitments"] = [{"start": 0, "end": len(u["text"]), "kind": "self", "owner": "洪玉麟",
                                  "action_start": 0, "action_end": len(u["text"])}]
        record = split.commitment_records(u, answer, [prediction(u["text"])])[0]
        self.assertEqual(1, record["correct"])
        legacy = {"cuts": [], "segments": [{"speakers": ["洪玉麟"]}], "self_present": True,
                  "self_owner": "洪玉麟", "reviewed": True}
        self.assertEqual(0, split.commitment_records(u, legacy, [prediction(u["text"])])[0]["correct"])

    def test_legacy_single_owner_is_not_reused_for_multiple_commitments(self):
        u = case(text="我来做方案；我来写报告")
        old = {"cuts": [], "segments": [{"speakers": ["洪玉麟"]}], "self_present": True,
               "self_owner": "洪玉麟", "reviewed": True}
        self.assertEqual([], split.gold_commitments(u, old))

    def test_named_rule_calibration_does_not_certify_wrong_assignee(self):
        u = case(text="春源，你周五把方案发我")
        from local_backend import commitment_spans
        span = commitment_spans(u["text"])[0]
        answer = truth()
        answer["commitments"] = [span | {"owner": "洪玉麟", "action_start": span["start"], "action_end": span["end"]}]
        config = {"named_calibrator": constant_model(NAMED_EVENT, ("span_chars", "explicit_named", "single_relation"))}
        records = split.commitment_records(u, answer, [prediction(u["text"])], config)
        self.assertTrue(records[0]["eligible"])
        self.assertEqual(0, records[0]["correct"])
        malformed = [prediction(u["text"], tier="C") | {"approx": True}]
        self.assertFalse(split.commitment_records(u, answer, malformed, config)[0]["eligible"])


class DatasetAndMetricsTest(unittest.TestCase):
    def test_actual_recordings_cannot_mix_enrollment_development_and_holdout(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(split, "ROOT", folder):
            wav = Path(folder) / "take.wav"
            classification = Path(folder) / "evidence/speaker-attribution/recording-classification.json"
            with self.assertRaises(ValueError):
                split.validate_recording_purpose(wav, "dev", "actual-chain")
            split.write(wav.with_suffix(".json"), {"purpose": "voiceprint-enrollment"})
            for partition in ("dev", "holdout"):
                with self.assertRaises(ValueError):
                    split.validate_recording_purpose(wav, partition, "actual-chain")
            split.write(wav.with_suffix(".json"), {"purpose": "unspecified"})
            split.write(classification, {"recordings": {wav.name: {"purpose": "meeting-development"}}})
            self.assertEqual("meeting-development", split.validate_recording_purpose(wav, "dev", "actual-chain"))
            with self.assertRaises(ValueError):
                split.validate_recording_purpose(wav, "holdout", "actual-chain")
            split.write(wav.with_suffix(".json"), {"purpose": "meeting-holdout"})
            with self.assertRaises(ValueError):
                split.recording_purpose(wav)
            self.assertEqual("historical-dji", split.validate_recording_purpose(wav, "dev", "dji"))

    def test_explicit_non_dji_corpus_uses_only_correctly_classified_scenes(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(split, "ROOT", folder):
            root, names = Path(folder), ["development.wav", "heldout.wav"]
            for index, name in enumerate(names):
                wav = root / name
                with wave.open(str(wav), "wb") as stream:
                    stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                    stream.writeframes(bytes([index, 0]) * 16000)
                split.write(wav.with_suffix(".json"), {"purpose": "meeting-development" if index == 0 else "meeting-holdout"})
                destination = root / "evidence/asr-words-test" / str(index)
                split.write(destination / "summary.json", {"recording": name, "complete": True, "transport_errors": 0})
                split.write(destination / "finals.json", [{"text": "甲乙", "start_time": 0, "end_time": 1000,
                    "words": [{"text": "甲", "start_time": 0, "end_time": 400}, {"text": "乙", "start_time": 500, "end_time": 900}]}])
            args = SimpleNamespace(dataset="actual-chain", dev_recording=[str(root / names[0])],
                                   holdout_recording=[str(root / names[1])], recordings_root=None)
            rows = split.corpus(args)
            self.assertEqual({"dev", "holdout"}, {row["partition"] for row in rows})
            split.validate_partitions({"cases": rows})
            split.write((root / names[0]).with_suffix(".json"), {"purpose": "voiceprint-enrollment"})
            with self.assertRaises(ValueError):
                split.corpus(args)

    def test_sentence_trial_fails_if_any_a_segment_fails_and_no_a_is_excluded(self):
        a, b = case(), case("u2")
        pred = {"u1": [prediction("甲", start=0, end=1000), prediction("乙", "林春源", start=1000, end=2000)],
                "u2": [prediction("甲乙", tier="B")]}
        result = split.metrics([a, b], {"u1": truth(), "u2": truth()}, pred)
        self.assertEqual(2, result["a_segment_count"])
        self.assertEqual(1, result["a_segment_wrong"])
        self.assertEqual(1, result["a_sentence_count"])
        self.assertEqual(1, result["a_sentence_wrong"])
        self.assertEqual(.5, result["a_coverage"])

    def test_equal_length_text_replacement_does_not_pass_coverage(self):
        with self.assertRaises(ValueError):
            split.metrics([case()], {"u1": truth()}, {"u1": [prediction("甲丙")]})

    def test_partitions_reject_same_scene_and_renamed_identical_audio(self):
        dev = case("d", partition="dev", recording="one.wav")
        hold = case("h", recording="one.wav")
        with self.assertRaises(ValueError):
            split.validate_partitions({"cases": [dev, hold]})
        hold["recording"] = "other.wav"
        dev["recording_hash"] = hold["recording_hash"] = "same-pcm"
        with self.assertRaises(ValueError):
            split.validate_partitions({"cases": [dev, hold]})

    def test_truth_requires_individual_action_binding_and_no_duplicate_span(self):
        u = case(text="我来做")
        body = {"manifest_hash": "test", "answers": {"u1": truth()}}
        body["answers"]["u1"]["commitments"] = [{"start": 0, "end": 3, "kind": "self", "owner": "洪玉麟", "action_start": 0, "action_end": 3}]
        split.validated_truth(body, {"hash": "test", "cases": [u]})
        invalid = copy.deepcopy(body)
        del invalid["answers"]["u1"]["commitments"][0]["action_end"]
        with self.assertRaises(ValueError):
            split.validated_truth(invalid, {"hash": "test", "cases": [u]})
        invalid = copy.deepcopy(body)
        invalid["answers"]["u1"]["commitments"] *= 2
        with self.assertRaises(ValueError):
            split.validated_truth(invalid, {"hash": "test", "cases": [u]})

    def test_dataset_paths_and_truth_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            a = split.dataset_paths(SimpleNamespace(dataset="dji", dataset_root=folder))
            b = split.dataset_paths(SimpleNamespace(dataset="remote", dataset_root=folder))
            split.write(a["truth"], {"marker": "one"})
            self.assertFalse(b["truth"].exists())
            with self.assertRaises(ValueError):
                split.dataset_paths(SimpleNamespace(dataset="../remote", dataset_root=folder))


class GatesAndGovernanceTest(unittest.TestCase):
    def good_metrics(self):
        return {"chars": 1000, "char_accuracy": .9, "wrong_claim_rate": 0,
                "change_count": 10, "change_recall": .9, "short_count": 10, "short_recall": .9, "short_wrong_rate": 0,
                "a_segment_count": 149, "a_segment_wrong": 0, "a_sentence_count": 149, "a_sentence_wrong": 0,
                "self_count": 80, "self_claimed": 59, "self_wrong": 0, "self_coverage": 59 / 80,
                "named_count": 80, "named_claimed": 59, "named_wrong": 0}

    def test_display_strict_and_both_owner_types_are_independent(self):
        base = {"char_accuracy": .5, "wrong_claim_rate": .05}
        good = self.good_metrics()
        self.assertEqual("pass", gate_statuses(good, base)["self_owner"])
        small = good | {"a_segment_count": 20, "a_sentence_count": 20}
        statuses = gate_statuses(small, base)
        self.assertEqual("pass", statuses["display"])
        self.assertEqual("insufficient_evidence", statuses["strict_a"])
        self.assertEqual("insufficient_evidence", gate_statuses(good | {"named_claimed": 0}, base)["named_owner"])
        self.assertEqual("pass", gate_statuses(good | {"named_claimed": 0}, base)["self_owner"])
        self.assertEqual("fail", gate_statuses(good | {"self_coverage": .59}, base)["self_owner"])

    def test_holdout_target_does_not_inspect_prediction_or_consume(self):
        policy = {"recordings": ["meeting.wav"], "minimum_cases": 149}
        ready, _ = frozen_holdout_ready({"cases": [case()]}, {"u1": truth()}, policy)
        self.assertFalse(ready)
        ledger = {"recordings": {}}
        self.assertEqual({}, ledger["recordings"])

    def test_consumed_holdout_allows_same_version_only_with_identical_truth(self):
        ledger = {}
        self.assertFalse(consume_holdout(ledger, ["meeting-1"], "v1", "frozen", "truth"))
        self.assertTrue(consume_holdout(ledger, ["meeting-1"], "v1", "frozen", "truth"))
        for version, frozen, truth_ in (("v2", "frozen", "truth"), ("v1", "changed", "truth"), ("v1", "frozen", "changed")):
            with self.assertRaises(ValueError):
                consume_holdout(ledger, ["meeting-1"], version, frozen, truth_)

    def test_concurrent_versions_cannot_both_consume_the_same_recording(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "ledger.json"
            def consume(version):
                try:
                    split.journal_consumption(path, ["same-recording"], version, version, "truth")
                    return "consumed"
                except ValueError:
                    return "rejected"
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(consume, ["v1", "v2"]))
            self.assertCountEqual(["consumed", "rejected"], results)
            self.assertEqual(1, len(split.read(path)["recordings"]))

    def test_prior_only_accepts_exact_nonoverlapping_high_confidence_identity(self):
        segment = prediction("甲乙")
        self.assertEqual("洪玉麟", split.speaker_prior(segment, {"thresholds": {"a": .98}}))
        for changed in ({"approx": True}, {"overlap": True}, {"confidence": None},
                        {"confidence": .8}, {"confidence": float("nan")}, {"speaker": None}):
            self.assertIsNone(split.speaker_prior(segment | changed, {"thresholds": {"a": .98}}))

    def test_default_disabled_report_and_stale_profile_never_enable(self):
        self.assertIsNone(verify_validation({"schema": 1}, "/tmp/nonexistent"))
        self.assertIsNone(verify_validation({"schema": 2, "finalized": False}, "/tmp/nonexistent"))
        with patch("local_backend.split_fingerprints", return_value={"active-profiles": "new"}):
            self.assertIsNone(verify_validation({"schema": 2, "finalized": True, "fingerprints": {"active-profiles": "old"}}, "/tmp/nonexistent"))

    def test_complete_schema2_fixture_verifies_only_explicit_passed_switches(self):
        # A full synthetic evidence bundle, with 149 real sentence IDs rather than copied counts.
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            dev = case("dev", partition="dev", recording="dev.wav")
            holdout = [case("h" + str(i), text="甲乙丙丁") for i in range(149)]
            for u in holdout:
                u["boundaries"] = [{"char": 2, "time_ms": 1000}]
            manifest = {"schema": 2, "dataset": "fixture", "cases": [dev] + holdout}
            manifest["hash"] = split.digest(manifest)
            answers = {u["id"]: truth() for u in manifest["cases"]}
            for u in holdout:
                answers[u["id"]] = truth(cuts=[2], segments=[{"speakers": ["洪玉麟"]}, {"speakers": ["林春源"]}])
            truth_file = {"manifest_hash": manifest["hash"], "answers": answers}
            config = {"calibrator": constant_model(SEGMENT_EVENT), "owner_calibrator": None,
                      "named_calibrator": None, "strict_a_enabled": False,
                      "self_owner_enabled": False, "named_owner_enabled": False}
            policy = {"recordings": ["meeting.wav"], "minimum_cases": 149}
            frozen = {"schema": 2, "dataset": "fixture", "version": "v1", "config": config,
                      "holdout_policy": policy}
            for name, value in (("manifest", manifest), ("truth", truth_file), ("frozen", frozen)):
                split.write(base / (name + ".json"), value)
            predictions = {u["id"]: [prediction("甲乙", end=1000), prediction("丙丁", "林春源", start=1000)] for u in holdout}
            final = split.metrics(holdout, answers, predictions)
            baseline = split.metrics(holdout, answers, {u["id"]: [prediction(u["text"], tier="B")] for u in holdout})
            report = {"schema": 2, "dataset": "fixture", "version": "v1", "finalized": True, "acceptance_scope": "actual-chain",
                      "fingerprints": {"active-profiles": "expected"}, "config": config,
                      "calibrator_hash": calibrator_hash(config), "manifest_hash": manifest["hash"],
                      "truth_hash": split.digest(truth_file), "frozen_hash": split.digest(frozen),
                      "dataset_files": {key: key + ".json" for key in ("manifest", "truth", "frozen")},
                      "holdout_policy": policy, "holdout_consumed": True, "holdout_count": 149,
                      "holdout_ids": [u["id"] for u in holdout], "final": final, "baseline": baseline,
                      "gate_statuses": gate_statuses(final, baseline), "switches": {}}
            with patch("local_backend.split_fingerprints", return_value=report["fingerprints"]):
                self.assertIsNone(verify_validation(report, base / "validation.json"))
                report["switches"] = {"display": True, "strict_a": True, "self_owner": True, "named_owner": True}
                result = verify_validation(report, base / "validation.json")
                self.assertTrue(result["split_display_enabled"])
                self.assertTrue(result["strict_a_enabled"])
                self.assertFalse(result["self_owner_enabled"])
                self.assertFalse(result["named_owner_enabled"])
                compact = report | {"kind": "signed-deployment-receipt", "holdout_recordings": ["meeting.wav"]}
                self.assertIsNone(verify_compact_validation(compact))
                self.assertTrue(verify_compact_validation(compact, signature_verified=True)["strict_a_enabled"])
                self.assertIsNone(verify_compact_validation(compact | {"acceptance_scope": "historical-dji"}, signature_verified=True))
                invalid = compact | {"gate_statuses": compact["gate_statuses"] | {"self_owner": "pass"}}
                self.assertIsNone(verify_compact_validation(invalid, signature_verified=True))
                split.write(base / "truth.json", truth_file | {"answers": {}})
                self.assertIsNone(verify_validation(report, base / "validation.json"))
            with patch("local_backend.split_fingerprints", return_value={"active-profiles": "changed"}):
                self.assertIsNone(verify_validation(report, base / "validation.json"))

    def test_bootstrap_metadata_is_explicitly_disabled_and_not_an_acceptance(self):
        report = disabled_validation()
        self.assertFalse(report["finalized"])
        self.assertFalse(any(report["switches"].values()))
        self.assertIsNone(verify_validation(report, "/tmp/no-report.json"))


class FixedLookPipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.args = SimpleNamespace(dataset="remote", dataset_root=self.tmp.name, version="v1", tune=True,
            registry_snapshot=str(Path(self.tmp.name) / "registry.json"), holdout_case_count=1,
            holdout_recording_count=None, holdout_cutoff=None, holdout_self_count=0, holdout_named_count=0,
            accept=.5, margin=.05, no_jev=True, a_threshold=.98, b_threshold=.5)
        Path(self.args.registry_snapshot).write_text("{}")
        self.paths = split.dataset_paths(self.args)
        self.dev, self.hold = case("d", partition="dev", recording="dev.wav"), case("h", recording="hold.wav")
        self.manifest = {"schema": 2, "dataset": "remote", "cases": [self.dev, self.hold]}
        self.manifest["hash"] = split.digest(self.manifest)
        self.answers = {"d": truth(), "h": truth()}
        split.write(self.paths["manifest"], self.manifest)
        self.save_truth()
        for u in (self.dev, self.hold):
            clip = self.paths["base"] / u["clip"]
            clip.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(clip), "wb") as out:
                out.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                out.writeframes(b"\0" * 64000)
        self.calls = []
        calls = self.calls
        class FakeSpeakers:
            def __init__(self, *args, **kwargs):
                self.ready, self.names, self.error = True, ["洪玉麟", "林春源"], None
            def scores(self, pcm):
                return [.8, .1]
            def split(self, pcm, text, words, start, context, jev, config, prior=None):
                calls.append(text)
                return [prediction(text, tier="B")], 1
        self.model_patch = patch.object(split, "Speakers", FakeSpeakers)
        self.fingerprint_patch = patch.object(split, "split_fingerprints", return_value={"active-profiles": "snapshot"})
        self.model_patch.start(); self.fingerprint_patch.start()
        self.addCleanup(self.model_patch.stop); self.addCleanup(self.fingerprint_patch.stop)
        self.print_patch = patch("builtins.print")
        self.print_patch.start()
        self.addCleanup(self.print_patch.stop)

    def save_truth(self):
        split.write(self.paths["truth"], {"manifest_hash": self.manifest["hash"], "answers": self.answers})

    def test_tune_uses_dev_only_and_freezes_null_one_class_calibration(self):
        self.hold["text"] = "保留文字不可看"
        self.manifest.pop("hash")
        self.manifest["hash"] = split.digest(self.manifest)
        split.write(self.paths["manifest"], self.manifest)
        self.answers["h"]["reviewed"] = False
        self.save_truth()
        frozen = split.evaluate(self.args)
        self.assertEqual("v1", frozen["version"])
        self.assertTrue(self.calls)
        self.assertNotIn(self.hold["text"], self.calls)
        self.assertIsNone(frozen["config"]["calibrator"])
        self.assertFalse(self.paths["ledger"].exists())
        self.assertFalse(self.paths["validation"].exists())

    def test_replacing_holdout_text_and_labels_does_not_change_frozen_config(self):
        first = split.evaluate(self.args)
        other_args = copy.copy(self.args)
        other_args.dataset = "remote-new"
        other_paths = split.dataset_paths(other_args)
        other_manifest = copy.deepcopy(self.manifest)
        other_manifest.pop("hash")
        other_manifest["dataset"] = "remote-new"
        other_manifest["cases"][1]["text"] = "完全不同的保留集文字"
        other_manifest["hash"] = split.digest(other_manifest)
        other_answers = copy.deepcopy(self.answers)
        other_answers["h"]["segments"] = [{"speakers": ["林春源"]}]
        split.write(other_paths["manifest"], other_manifest)
        split.write(other_paths["truth"], {"manifest_hash": other_manifest["hash"], "answers": other_answers})
        (other_paths["base"] / self.dev["clip"]).parent.mkdir(parents=True, exist_ok=True)
        (other_paths["base"] / self.dev["clip"]).write_bytes((self.paths["base"] / self.dev["clip"]).read_bytes())
        self.calls.clear()
        second = split.evaluate(other_args)
        self.assertEqual(first["config"], second["config"])
        self.assertEqual(first["development"], second["development"])
        self.assertNotIn(other_manifest["cases"][1]["text"], self.calls)

    def test_insufficient_fixed_cohort_is_not_predicted_or_consumed(self):
        self.args.holdout_case_count = 149
        split.evaluate(self.args)
        self.calls.clear()
        self.args.tune = False
        result = split.evaluate(self.args)
        self.assertEqual("insufficient_evidence", result["status"])
        self.assertFalse(result["predictions_inspected"])
        self.assertEqual([], self.calls)
        self.assertFalse(self.paths["ledger"].exists())
        self.assertFalse(self.paths["validation"].exists())

    def test_final_look_default_switches_off_and_same_result_is_reproduced(self):
        split.evaluate(self.args)
        self.args.tune = False
        report = split.evaluate(self.args)
        self.assertTrue(report["finalized"])
        self.assertFalse(any(report["switches"].values()))
        self.assertEqual("insufficient_evidence", report["gate_statuses"]["strict_a"])
        self.calls.clear()
        self.assertEqual(report, split.evaluate(self.args))
        self.assertEqual([], self.calls)
        self.answers["h"]["segments"] = [{"speakers": ["林春源"]}]
        self.save_truth()
        with self.assertRaises(ValueError):
            split.evaluate(self.args)


if __name__ == "__main__":
    unittest.main()
