import json
import os
import tempfile
import unittest

from simulator_mcp.proxy.network_log import NetworkLog


class NetworkLogTests(unittest.TestCase):
    def test_add_writes_summary_and_detail_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_log = os.path.join(tmpdir, "proxy_requests.log")
            detail_log = os.path.join(tmpdir, "proxy_requests.jsonl")
            body_dir = os.path.join(tmpdir, "bodies")
            log = NetworkLog(
                summary_log_file=summary_log,
                detail_log_file=detail_log,
                body_log_dir=body_dir,
            )

            log.add(
                method="POST",
                url="https://example.com/api/create_payment_intent",
                status_code=200,
                request_headers={"content-type": "application/json"},
                response_headers={"content-type": "application/json"},
                request_body='{"amount":"50"}',
                response_body='{"ok":true}',
                duration_ms=123.4,
            )

            with open(summary_log, encoding="utf-8") as f:
                summary_content = f.read()
            self.assertIn("POST", summary_content)
            self.assertIn("/api/create_payment_intent", summary_content)

            with open(detail_log, encoding="utf-8") as f:
                detail_entry = json.loads(f.readline())
            self.assertEqual(detail_entry["request_body"], '{"amount":"50"}')
            self.assertEqual(detail_entry["response_body"], '{"ok":true}')
            self.assertIsNone(detail_entry["request_body_file"])
            self.assertIsNone(detail_entry["response_body_file"])

    def test_query_falls_back_to_persisted_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_log = os.path.join(tmpdir, "proxy_requests.log")
            detail_log = os.path.join(tmpdir, "proxy_requests.jsonl")
            body_dir = os.path.join(tmpdir, "bodies")

            writer = NetworkLog(
                summary_log_file=summary_log,
                detail_log_file=detail_log,
                body_log_dir=body_dir,
            )
            writer.add(
                method="POST",
                url="https://example.com/api/create_payment_intent",
                status_code=200,
                request_headers={},
                response_headers={},
                request_body='{"task_id":"123"}',
                response_body='{"order_id":"abc"}',
                duration_ms=88,
            )

            reader = NetworkLog(
                summary_log_file=summary_log,
                detail_log_file=detail_log,
                body_log_dir=body_dir,
            )
            results = reader.query(url_pattern="create_payment_intent", method="POST", limit=5)

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["request_body"], '{"task_id":"123"}')
            self.assertEqual(results[0]["response_body"], '{"order_id":"abc"}')

    def test_large_bodies_spill_to_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            summary_log = os.path.join(tmpdir, "proxy_requests.log")
            detail_log = os.path.join(tmpdir, "proxy_requests.jsonl")
            body_dir = os.path.join(tmpdir, "bodies")
            large_body = "x" * 64
            log = NetworkLog(
                summary_log_file=summary_log,
                detail_log_file=detail_log,
                body_log_dir=body_dir,
                body_inline_limit=16,
            )

            log.add(
                method="GET",
                url="https://example.com/assets/index.bundle",
                status_code=200,
                request_headers={},
                response_headers={"content-type": "application/javascript"},
                request_body=None,
                response_body=large_body,
                duration_ms=5,
            )

            results = log.query(url_pattern="index.bundle", method="GET", limit=1)
            self.assertEqual(len(results), 1)
            self.assertIsNone(results[0]["response_body"])
            self.assertTrue(results[0]["response_body_file"])

            with open(results[0]["response_body_file"], encoding="utf-8") as f:
                self.assertEqual(f.read(), large_body)
