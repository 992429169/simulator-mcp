import asyncio
import unittest
from unittest import mock

from simulator_mcp.tools import network


class NetworkToolTests(unittest.TestCase):
    def test_start_network_proxy_rejects_local_mode(self):
        with self.assertRaisesRegex(ValueError, "only supports regular mode"):
            asyncio.run(network.start_network_proxy({"mode": "local"}))

    def test_start_network_proxy_rejects_legacy_local_arguments(self):
        with self.assertRaisesRegex(ValueError, "remove capture_frontmost_app"):
            asyncio.run(
                network.start_network_proxy({"capture_frontmost_app": True})
            )

    def test_start_network_proxy_accepts_regular_mode_for_compatibility(self):
        proxy = mock.Mock()
        proxy.start.return_value = "Proxy started on port 18080."

        with mock.patch("simulator_mcp.tools.network.get_proxy_server", return_value=proxy):
            result = asyncio.run(
                network.start_network_proxy(
                    {"mode": "regular", "port": 18080, "udid": "TEST-UDID"}
                )
            )

        proxy.start.assert_called_once_with(port=18080, udid="TEST-UDID")
        self.assertEqual(result, "Proxy started on port 18080.")
