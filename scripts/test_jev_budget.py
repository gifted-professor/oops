#!/usr/bin/env python3
"""Jev calls: one retry inside the same budget for stalls and 5xx, none for a 4xx, and fallbacks are counted."""
import io
import unittest
import urllib.error

import local_backend
from local_backend import Backend, Jev, TagCache

ANSWER = ({"q": {"choice": "commitment", "probabilities": {"commitment": .9, "idea": .1}}}, "jev-test")


def http_error(code):
    return urllib.error.HTTPError("https://jev", code, "x", {}, io.BytesIO(b"{}"))


class ScriptedJev(Jev):
    def __init__(self, outcomes):
        super().__init__("key", "jev-test")
        self.outcomes, self.timeouts = list(outcomes), []

    def ask(self, body, questions, timeout):
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class JevBudgetTest(unittest.TestCase):
    def test_stall_then_success_shares_one_budget(self):
        jev = ScriptedJev([TimeoutError("stalled"), ANSWER])
        answers, model = jev.choices({}, {"q": {}}, timeout=7.0, retries=1)
        self.assertEqual("commitment", answers["q"]["choice"])
        self.assertEqual([3.5, 3.5], jev.timeouts)  # never longer than the 7 s the caller allowed
        self.assertEqual({"calls": 2, "retries": 1, "failures": 1}, {k: jev.stats[k] for k in ("calls", "retries", "failures")})

    def test_server_error_is_retried_but_a_4xx_is_not(self):
        jev = ScriptedJev([http_error(503), ANSWER])
        jev.choices({}, {"q": {}}, timeout=6.0, retries=1)
        self.assertEqual(2, jev.stats["calls"])
        region = ScriptedJev([http_error(451), ANSWER])
        with self.assertRaises(urllib.error.HTTPError):
            region.choices({}, {"q": {}}, timeout=6.0, retries=1)
        self.assertEqual((1, 0), (region.stats["calls"], region.stats["retries"]))

    def test_two_failures_raise_and_default_has_no_retry(self):
        jev = ScriptedJev([TimeoutError(), TimeoutError()])
        with self.assertRaises(TimeoutError):
            jev.choices({}, {"q": {}}, timeout=7.0, retries=1)
        self.assertEqual((2, 1, 2), (jev.stats["calls"], jev.stats["retries"], jev.stats["failures"]))
        once = ScriptedJev([TimeoutError(), ANSWER])
        with self.assertRaises(TimeoutError):
            once.choices({}, {"q": {}}, timeout=6.0)
        self.assertEqual([6.0], once.timeouts)

    def test_tag_falls_back_to_rules_and_counts_it(self):
        backend = Backend.__new__(Backend)
        backend.jev, backend.tags = ScriptedJev([http_error(451)]), TagCache()
        result = backend.tag({"session_id": "s", "utterance_id": "u1", "text": "我明天把方案发你", "start_ms": 0, "end_ms": 1000})
        self.assertEqual("rules", result["engine"])
        self.assertEqual(1, backend.jev.stats["tag_fallbacks"])


if __name__ == "__main__":
    unittest.main()
