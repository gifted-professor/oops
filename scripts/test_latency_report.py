#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

import latency_report


class LatencyReportTest(unittest.TestCase):
    def test_percentiles_server_fields_and_bad_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            session = os.path.join(folder, "session-0001")
            os.makedirs(session)
            lines = [json.dumps({"id": f"u{i}", "stage": "speaker", "http_ms": 100 * i,
                                 "server": {"identify_ms": 10 * i}}) for i in range(1, 11)]
            lines += ["not json", json.dumps({"stage": "final", "final_lag_ms": 420}),
                      json.dumps({"stage": "speaker", "http_ms": -5}), json.dumps({"id": "x"}),
                      json.dumps({"stage": "split", "status": "done", "since_final_ms": 6000})]
            with open(os.path.join(session, "timing.jsonl"), "w") as f:
                f.write("\n".join(lines))
            table, sessions = latency_report.collect(latency_report.find([folder]))
            rows = {(r["stage"], r["field"]): r for r in latency_report.summarise(table)}
            self.assertEqual(1, sessions)
            self.assertEqual((10, 500, 900, 1000), tuple(rows[("speaker", "http_ms")][k] for k in ("n", "p50", "p90", "max")))
            self.assertEqual(50, rows[("speaker", "server.identify_ms")]["p50"])
            self.assertEqual(420, rows[("final", "final_lag_ms")]["p50"])
            self.assertEqual(6000, rows[("split", "since_final_ms")]["max"])
            self.assertNotIn(("split", "status"), rows)

    def test_no_data_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(1, latency_report.main([folder]))


if __name__ == "__main__":
    unittest.main()
