import unittest
from unittest.mock import AsyncMock, Mock, patch

from simulator_mcp.tools import device


class DeviceToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_launch_app_with_proxy_installs_cert_and_merges_env(self):
        proxy = Mock()
        proxy.is_running = True
        proxy.ensure_ca_cert_installed.return_value = "CA cert installed on TEST-UDID."
        proxy.get_launch_env.return_value = {
            "PROXY_HOST": "127.0.0.1",
            "PROXY_PORT": "8080",
        }

        with (
            patch("simulator_mcp.proxy.proxy_server.get_proxy_server", return_value=proxy),
            patch(
                "simulator_mcp.tools.device.simctl.launch_app",
                new=AsyncMock(return_value="Launched com.example.app on TEST-UDID."),
            ) as launch_app,
        ):
            result = await device.launch_app(
                {
                    "udid": "TEST-UDID",
                    "bundle_id": "com.example.app",
                    "env": {"FOO": "bar"},
                    "proxy": True,
                }
            )

        proxy.ensure_ca_cert_installed.assert_called_once_with("TEST-UDID")
        launch_app.assert_awaited_once_with(
            "TEST-UDID",
            "com.example.app",
            args=None,
            env={
                "FOO": "bar",
                "PROXY_HOST": "127.0.0.1",
                "PROXY_PORT": "8080",
            },
        )
        self.assertEqual(
            result,
            "Launched com.example.app on TEST-UDID. CA cert installed on TEST-UDID.",
        )
