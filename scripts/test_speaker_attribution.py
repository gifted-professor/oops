"""Attribution safeguards with synthetic audio decisions; not real-device acceptance."""
import ast
import copy
import math
import threading
import types
import unittest

from local_backend import (Backend, TEAM, active_voiceprint_hash, adjudicate_segments, align_words,
                           canonical_ast, commitment_evidence, commitment_spans, owner_resolution, registration_generation, viterbi_speakers,
                           voiceprint_decision)
from speaker_attribution import (SEGMENT_EVENT, OWNER_EVENT, attribute, calibrated_probability,
                                 activity_boundaries, consolidate_trial, merge_for_display, unknown_segment)


def calibration(event, probability=.99):
    key = "span_chars" if event == "named_action_binding_correct" else "top_score"
    return {"schema": 1, "event": event, "features": [key], "weights": [0.0],
            "mean": [0.0], "scale": [1.0], "intercept": math.log(probability / (1 - probability))}


def segment(text="我来做这个", **changes):
    return {"text": text, "start_ms": 0, "end_ms": 2000, "speaker": TEAM[2], "score": .91,
            "source": "voiceprint", "approx": False, "overlap": False, "tier": "A", "confidence": .99,
            "candidates": [{"name": TEAM[2], "score": .91}, {"name": TEAM[3], "score": .2}], **changes}


class AttributionTest(unittest.TestCase):
    def test_trial_consolidation_rescores_runs_leaning_to_one_voice(self):
        weak = lambda text, a, b, first: segment(text, start_ms=a, end_ms=b, speaker=None, source=None, tier="C",
            candidates=[{"name": first, "score": .45}, {"name": TEAM[3], "score": .2}])
        parts = [weak("你跟我", 0, 1000, TEAM[0]), weak("聊聊天", 1000, 2000, TEAM[0]),
                 weak("好啊", 2000, 3000, TEAM[2])]
        calls = []
        speakers = types.SimpleNamespace(names=list(TEAM), accept=.5, margin=.05,
            scores=lambda pcm: calls.append(len(pcm)) or [.62, .1, .3, .2])
        out = consolidate_trial(parts, b"\0" * 96000, 0, speakers, voiceprint_decision)
        self.assertEqual(["你跟我聊聊天", "好啊"], [o["text"] for o in out])
        self.assertEqual(TEAM[0], out[0]["speaker"])
        self.assertEqual("B", out[0]["tier"])
        self.assertEqual([64000], calls)  # one re-score of the joined 2 s run only
        self.assertIsNone(out[1]["speaker"])

    def test_one_sentence_is_not_broken_into_single_character_lines(self):
        # Seen live: "现 | 在个 | 那 | 我就把这个 api 抽上 | 来", every part unnamed and over a second long.
        unknown = lambda text, a, b: segment(text, start_ms=a, end_ms=b, speaker=None, source=None, tier="C")
        parts = [unknown("现", 0, 1200), unknown("在个", 1200, 2600), unknown("那", 2600, 3900),
                 unknown("我就把这个 api 抽上", 3900, 7000), unknown("来", 7000, 8200)]
        merged = merge_for_display(parts)
        self.assertEqual("现在个那我就把这个 api 抽上来", "".join(m["text"] for m in merged))
        self.assertTrue(all(len(m["text"]) >= 6 for m in merged), [m["text"] for m in merged])
        self.assertEqual((0, 8200), (merged[0]["start_ms"], merged[-1]["end_ms"]))
        # A short named part is information and stays; only unnamed fragments are folded.
        named = [segment("嗯好", start_ms=0, end_ms=1500, speaker=TEAM[0]), unknown("我们接着往下说吧", 1500, 5000)]
        self.assertEqual(["嗯好", "我们接着往下说吧"], [m["text"] for m in merge_for_display(named)])

    def test_trial_display_folds_fragments_without_losing_text(self):
        parts = [segment("林说了一大段", start_ms=0, end_ms=3000, speaker=TEAM[0]),
                 segment("嗯", start_ms=3000, end_ms=3300, speaker=None, overlap=True, tier="C"),
                 segment("还是林", start_ms=3300, end_ms=4500, speaker=TEAM[0]),
                 segment("对", start_ms=4500, end_ms=4800, speaker=None, tier="C"),
                 segment("洪接着说", start_ms=4800, end_ms=7000, speaker=TEAM[2]),
                 segment("两个人一起说", start_ms=7000, end_ms=8500, speaker=None, overlap=True, tier="C")]
        merged = merge_for_display(parts)
        self.assertEqual("".join(p["text"] for p in parts), "".join(m["text"] for m in merged))
        self.assertEqual([TEAM[0], TEAM[2], None], [m["speaker"] for m in merged])
        self.assertTrue(merged[-1]["overlap"])  # a real (>=1 s) overlap stays visible
        self.assertEqual([0, 4800, 7000], [m["start_ms"] for m in merged])

    def test_v2_canonical_ast_ignores_only_empty_new_type_parameters(self):
        current = ast.parse('def decision(x): return x + .5\n').body[0]
        old_shape = copy.deepcopy(current)
        old_shape.__dict__.pop('type_params', None)
        old_shape._fields = tuple(name for name in old_shape._fields if name != 'type_params')
        current.type_params = []
        self.assertEqual(canonical_ast(old_shape), canonical_ast(current))
        current.type_params = [ast.Name(id='T', ctx=ast.Load())]
        self.assertNotEqual(canonical_ast(old_shape), canonical_ast(current))

    def predict(self, activities, config=None, text="甲乙丙", words=None, scores=(.9, .2)):
        speaker = types.SimpleNamespace(names=TEAM[2:], accept=.5, margin=.05, scores=lambda pcm: list(scores))
        words = words if words is not None else [
            {"text": "甲", "start_ms": 0, "end_ms": 300},
            {"text": "乙", "start_ms": 300, "end_ms": 600},
            {"text": "丙", "start_ms": 600, "end_ms": 1000}]
        return attribute(speaker, b"\0" * 32000, text, words, 0, align_words,
                         viterbi_speakers, voiceprint_decision, adjudicate_segments,
                         activities, config=config)[0]

    def test_overlap_projection_blocks_names_and_calibration(self):
        activities = [{"start_ms": 0, "end_ms": 450, "kind": "speech"},
                      {"start_ms": 450, "end_ms": 650, "kind": "overlap"},
                      {"start_ms": 650, "end_ms": 1000, "kind": "speech"}]
        result = self.predict(activities, {"calibrator": calibration(SEGMENT_EVENT), "strict_a_enabled": True})
        self.assertEqual("甲乙丙", "".join(s["text"] for s in result))
        for s in result:
            if "乙" in s["text"] or "丙" in s["text"]:
                self.assertTrue(s["overlap"])
                self.assertIsNone(s["speaker"])
                self.assertIsNone(s["confidence"])
                self.assertEqual("C", s["tier"])
                self.assertLessEqual(len(s["candidates"]), 2)
        first = next(s for s in result if s["text"] == "甲")
        self.assertEqual(1.0, first["_features"]["near_overlap"])

    def test_missing_activity_or_alignment_returns_full_unknown(self):
        for result in (self.predict(None), self.predict([]), self.predict([], words=[]), self.predict([], text="完全不同")):
            self.assertEqual(1, len(result))
            self.assertIsNone(result[0]["speaker"])
            self.assertEqual("C", result[0]["tier"])
            self.assertIsNone(result[0]["confidence"])
        silent = self.predict([{"start_ms": 0, "end_ms": 1000, "kind": "silence"}],
                              {"calibrator": calibration(SEGMENT_EVENT), "strict_a_enabled": True})
        self.assertTrue(all(s["speaker"] is None and s["tier"] == "C" for s in silent))

    def test_null_probability_and_strict_gate(self):
        activities = [{"start_ms": 0, "end_ms": 1000, "kind": "speech"}]
        uncalibrated = self.predict(activities, {"strict_a_enabled": True})[0]
        self.assertIsNone(uncalibrated["confidence"])
        self.assertEqual("B", uncalibrated["tier"])
        config = {"calibrator": calibration(SEGMENT_EVENT)}
        self.assertEqual("B", self.predict(activities, config)[0]["tier"])
        self.assertEqual("A", self.predict(activities, config | {"strict_a_enabled": True})[0]["tier"])

    def test_local_slots_do_not_create_cross_window_identity(self):
        activities = [{"start_ms": 0, "end_ms": 500, "kind": "speech", "_slot": (0, (0,))},
                      {"start_ms": 500, "end_ms": 700, "kind": "speech", "_slot": (1, (1,))},
                      {"start_ms": 700, "end_ms": 1000, "kind": "speech", "_slot": (1, (2,))}]
        self.assertEqual([700], activity_boundaries(activities))

    def test_jev_cannot_assign_overlap_or_change_locked_identity(self):
        class Jev:
            def choices(inner, state, questions):
                self.assertEqual({"s2"}, set(questions))
                return {"s2": {"choice": "p2", "probabilities": {"p2": 1}}}, "fake"
        locked = segment(scores=[.9, .2, .1])
        overlap = segment(speaker=None, source=None, overlap=True, scores=[.9, .2, .1])
        uncertain = segment(speaker=None, source=None, scores=[.49, .45, .1])
        adjudicate_segments(Jev(), [locked, overlap, uncertain], TEAM[:3])
        self.assertEqual(TEAM[2], locked["speaker"])
        self.assertIsNone(overlap["speaker"])
        self.assertIsNone(uncertain["speaker"])

    def test_soft_prior_does_not_override_strong_audio(self):
        self.assertEqual([1, 1], viterbi_speakers([[.2, .9], [.2, .9]], initial_bias=[.3, 0, 0]))
        with self.assertRaises(ValueError):
            viterbi_speakers([[.9]], switch_costs=[float("nan")])

    def test_wrong_event_or_nonfinite_model_has_no_probability(self):
        self.assertIsNone(calibrated_probability({"top_score": .9}, calibration(SEGMENT_EVENT), OWNER_EVENT))
        self.assertIsNone(calibrated_probability({"top_score": .9}, calibration(OWNER_EVENT) | {"weights": [float("nan")]}, OWNER_EVENT))


class OwnerSafetyTest(unittest.TestCase):
    def setUp(self):
        self.config = {"owner_calibrator": calibration(OWNER_EVENT),
                       "named_calibrator": calibration("named_action_binding_correct")}

    def request(self, pieces=None, text="我来做这个"):
        return {"utterances": [{"id": "u1", "text": text, "segments": pieces if pieces is not None else [segment(text)]}]}

    def test_collective_quotes_negations_and_multiple_commitments(self):
        for text in ("我们下周完成", "咱们我来做", "大家一起做", "他说我来做", "我不会做", "我来做吗？"):
            self.assertEqual(("unknown", None), commitment_evidence(text), text)
        text = "我来做；春源，你周五前提交方案"
        spans = commitment_spans(text)
        self.assertEqual(2, len(spans))
        self.assertTrue(all(text[s["start"]:s["end"]] == s["text"] for s in spans))
        result = owner_resolution(self.request(text=text), self.config, True, True, True)
        self.assertIsNone(result["owner"])
        self.assertIsNone(result["suggested_owner"])
        self.assertIn("拆", result["reason"])

    def test_segment_probability_is_not_owner_probability(self):
        result = owner_resolution(self.request(), {"calibrator": calibration(SEGMENT_EVENT)}, True, True)
        self.assertIsNone(result["owner_confidence"])
        self.assertIsNone(result["owner"])
        self.assertEqual(TEAM[2], result["suggested_owner"])
        self.assertEqual("B", result["tier"])
        self.assertEqual(TEAM[2], owner_resolution(self.request(), self.config, True, True)["owner"])
        for strict, own in ((False, True), (True, False)):
            self.assertIsNone(owner_resolution(self.request(), self.config, strict, own)["owner"])

    def test_approx_overlap_unknown_mismatch_and_crossing_have_no_owner_or_suggestion(self):
        cases = [[segment(approx=True)], [segment(overlap=True)], [segment(speaker=None)],
                 [segment("别的原话")], [segment("我来"), segment("做这个")]]
        for pieces in cases:
            result = owner_resolution(self.request(pieces), self.config, True, True)
            self.assertEqual("C", result["tier"])
            self.assertIsNone(result["owner"])
            self.assertIsNone(result["suggested_owner"])

    def test_named_gate_is_independent_and_calibrated(self):
        req = self.request(text="春源，你周五前提交方案")
        default = owner_resolution(req)
        self.assertIsNone(default["owner"])
        self.assertEqual(TEAM[3], default["suggested_owner"])
        self.assertIsNone(owner_resolution(req, self.config, True, True, False)["owner"])
        self.assertIsNone(owner_resolution(req, {}, True, True, True)["owner"])
        self.assertEqual(TEAM[3], owner_resolution(req, self.config, True, False, True)["owner"])
        self_named = self.request([segment(req["utterances"][0]["text"], speaker=TEAM[3])], text=req["utterances"][0]["text"])
        excluded = owner_resolution(self_named, self.config, True, False, True)
        self.assertIsNone(excluded["owner"])
        self.assertEqual("C", excluded["tier"])
        self_named["utterances"][0]["segments"][0].update(tier="B", confidence=None)
        self.assertEqual(TEAM[3], owner_resolution(self_named, self.config, True, False, True)["owner"])
        for pieces in ([segment(req["utterances"][0]["text"], overlap=True)],
                       [segment(req["utterances"][0]["text"], approx=True)],
                       [segment("春源，你"), segment("周五前提交方案")]):
            result = owner_resolution(self.request(pieces, text=req["utterances"][0]["text"]), self.config, True, True, True)
            self.assertIsNone(result["owner"])
            self.assertIsNone(result["suggested_owner"])
            self.assertEqual("C", result["tier"])
        missing = self.request([], text=req["utterances"][0]["text"])
        self.assertIsNone(owner_resolution(missing, self.config, True, True, True)["owner"])

    def test_context_named_person_cannot_become_current_owner(self):
        req = self.request(text="方案下周提交") | {"context": [{"text": "玉麟，你周五前提交方案"}]}
        self.assertIsNone(owner_resolution(req, self.config, True, True, True)["owner"])


class BackendEvidenceTest(unittest.TestCase):
    def backend(self):
        b = Backend.__new__(Backend)
        b.speaker_state_lock = threading.RLock()
        b.speaker_priors, b.speaker_evidence = {}, {}
        b.split_config = {"owner_calibrator": calibration(OWNER_EVENT)}
        b.strict_a_enabled = b.self_owner_enabled = b.named_owner_enabled = True
        b.speakers = types.SimpleNamespace(registry=types.SimpleNamespace(data={"people": {TEAM[2]: {"vector": [1, 0], "source": "remote"}}}))
        b.validated_voiceprint_hash = active_voiceprint_hash(b.speakers)
        b.validated_registration_generation = registration_generation(b.speakers)
        b.jev = None
        return b

    def test_context_resolved_task_only_suggests_owner_from_server_split(self):
        b = self.backend()
        b.split_config, b.strict_a_enabled = None, False
        b.speaker_state_lock = threading.RLock()
        proposal = {"tasks": [{"is_task": True, "task_quote": "那你把那个顺便发我吧", "what": "把运营选题资料发给对方",
                               "owner_role": "addressee", "owner_name": None, "due_raw": None, "missing": [],
                               "resolutions": [{"phrase": "那个", "meaning": "运营选题资料",
                                                "evidence": [{"id": "u53", "quote": "你说运营那个"}]}]}]}
        b.resolver = types.SimpleNamespace(complete_json=lambda system, user, timeout: (proposal, "fake"))
        text = "诶，那你把那个顺便发我吧，我感觉我可以一起。"
        b.remember_trial({"session_id": "s", "utterance_id": "u54", "text": text},
                         [{"text": "诶，那你把那个顺便发我吧，", "speaker": TEAM[2], "overlap": False},
                          {"text": "我感觉我可以一起。", "speaker": TEAM[0], "overlap": False}])
        b.remember_names("s", [TEAM[0], TEAM[2]])
        draft = b.draft({"session_id": "s", "utterances": [{"id": "u54", "text": text}],
                         "context": [{"id": "u53", "text": "跑一下那个，你说运营那个。"}]})
        self.assertEqual("把运营选题资料发给对方", draft["what"])
        self.assertIsNone(draft["owner"])
        self.assertEqual(TEAM[0], draft["suggested_owner"])  # addressee of TEAM[2] in a two-person meeting
        self.assertEqual("B", draft["tier"])
        self.assertEqual("运营选题资料", draft["task_context"]["resolutions"][0]["meaning"])
        b.resolver = types.SimpleNamespace(complete_json=lambda *a: (_ for _ in ()).throw(TimeoutError()))
        b.task_cache.clear() if hasattr(b, "task_cache") else None
        fallback = b.draft({"session_id": "s2", "utterances": [{"id": "u1", "text": "我周五前把方案发你"}], "context": []})
        self.assertIsNone(fallback["task_context"])
        self.assertEqual("我周五前把方案发你", fallback["what"])

    def test_profiles_not_metadata_determine_acceptance_hash(self):
        b = self.backend()
        first = active_voiceprint_hash(b.speakers)
        person = b.speakers.registry.data["people"][TEAM[2]]
        person["updated_at"] = "later"
        self.assertEqual(first, active_voiceprint_hash(b.speakers))
        person["profiles"] = {"remote": {"vector": [1, 0]}, "mic": {"vector": [0, 1]}}
        self.assertNotEqual(first, active_voiceprint_hash(b.speakers))
        b.refresh_attribution_gate()
        self.assertIsNone(b.split_config)
        self.assertFalse(b.strict_a_enabled)

    def test_reenrollment_with_identical_vector_invalidates_registration_generation(self):
        b = self.backend()
        vector_hash, generation = b.validated_voiceprint_hash, b.validated_registration_generation
        b.speakers.registry.data['people'][TEAM[2]].update(sample_id='recorded-again', updated_at='later')
        self.assertEqual(vector_hash, active_voiceprint_hash(b.speakers))
        self.assertNotEqual(generation, registration_generation(b.speakers))
        b.refresh_attribution_gate()
        self.assertIsNone(b.split_config)
        self.assertFalse(b.self_owner_enabled)

    def test_priors_require_same_session_order_and_no_discontinuity(self):
        b = self.backend()
        req = {"session_id": "s", "utterance_id": "u1", "clip_start_ms": 0, "text": "我来做这个"}
        b.remember_speaker(req, [segment()])
        self.assertIsNone(b.speaker_prior(req | {"session_id": "other", "utterance_id": "u2", "clip_start_ms": 2000}))
        self.assertIsNone(b.speaker_prior(req | {"utterance_id": "u2", "clip_start_ms": 1000}))
        self.assertEqual(TEAM[2], b.speaker_prior(req | {"utterance_id": "u2", "clip_start_ms": 2000}))
        b.remember_speaker(req, [segment()])
        self.assertIsNone(b.speaker_prior(req | {"utterance_id": "u2", "clip_start_ms": 0, "audio_discontinuity": True}))
        self.assertEqual({}, b.speaker_priors)
        b.remember_speaker(req, [segment()])
        self.assertIsNone(b.speaker_prior(req | {"utterance_id": "u2", "clip_start_ms": 8000}))

    def test_phone_cannot_upgrade_its_own_segment_to_prefill(self):
        b = self.backend()
        req = {"session_id": "s", "utterances": [{"id": "u1", "text": "我来做这个", "segments": [segment()]}]}
        result = b.draft(req)
        self.assertIsNone(result["owner"])
        self.assertIsNone(result["who"])
        self.assertEqual(TEAM[2], result["suggested_owner"])
        b.remember_speaker({"session_id": "s", "utterance_id": "u1", "text": "我来做这个"}, [segment()])
        result = b.draft(req)
        self.assertEqual(TEAM[2], result["owner"])
        self.assertEqual(result["owner"], result["who"])


if __name__ == "__main__":
    unittest.main()
