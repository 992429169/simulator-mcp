import threading
import time
import unittest

from simulator_mcp.proxy.mock_engine import MockEngine


class MockEngineTests(unittest.TestCase):
    def test_invalid_regex_is_rejected_on_add(self):
        engine = MockEngine()

        with self.assertRaisesRegex(ValueError, "Invalid url_pattern"):
            engine.add_rule("(")

    def test_find_match_is_safe_during_concurrent_mutation(self):
        engine = MockEngine()
        for i in range(100):
            engine.add_rule(f"rule{i}")

        stop = threading.Event()
        errors: list[Exception] = []

        def writer():
            index = 100
            while not stop.is_set():
                try:
                    rule = engine.add_rule(f"rule{index}")
                    if index % 2 == 0:
                        engine.remove_rule(rule.id)
                    index += 1
                except Exception as exc:  # pragma: no cover - only hit on regression.
                    errors.append(exc)
                    stop.set()

        def reader():
            deadline = time.time() + 0.25
            while time.time() < deadline and not stop.is_set():
                try:
                    engine.find_match("https://example.com/rule1", "GET")
                except Exception as exc:  # pragma: no cover - only hit on regression.
                    errors.append(exc)
                    stop.set()
            stop.set()

        threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(errors, [])
