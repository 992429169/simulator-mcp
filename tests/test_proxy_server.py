import time
import unittest
from subprocess import CompletedProcess
from unittest import mock

from simulator_mcp.proxy.mock_engine import MockEngine
from simulator_mcp.proxy.network_log import NetworkLog
from simulator_mcp.proxy.proxy_server import ProxyAddon, ProxyServer


class SuccessfulProxyServer(ProxyServer):
    def _run_proxy(self):
        self._mark_started()
        while self.is_running:
            time.sleep(0.01)


class FailedProxyServer(ProxyServer):
    def _run_proxy(self):
        self._mark_startup_failed(RuntimeError("bind failed"))


class ProxyServerTests(unittest.TestCase):
    def test_proxy_addon_logs_failed_flow_errors(self):
        addon = ProxyAddon(NetworkLog(), MockEngine())

        request = mock.Mock(
            method="GET",
            pretty_url="https://example.com/fail",
            headers={"accept": "*/*"},
            content=None,
        )
        flow = mock.Mock(
            id="flow-1",
            request=request,
            response=None,
            error=RuntimeError("TLS handshake failed"),
        )
        addon._start_times[flow.id] = time.time() - 0.1

        addon.error(flow)

        entry = addon.network_log.query(url_pattern="example.com/fail", limit=1)[0]
        self.assertEqual(entry["method"], "GET")
        self.assertIsNone(entry["status_code"])
        self.assertEqual(entry["error"], "TLS handshake failed")

    def test_start_waits_for_real_ready_signal(self):
        server = SuccessfulProxyServer()
        with mock.patch.object(
            server,
            "ensure_ca_cert_installed",
            return_value="CA cert installed on TEST-UDID.",
        ) as ensure_cert:
            message = server.start(port=18080)

        ensure_cert.assert_called_once_with(None)
        self.assertIn("Proxy started on port 18080.", message)
        self.assertTrue(server.is_running)
        self.assertEqual(server.stop(), "Proxy stopped.")

    def test_start_uses_explicit_udid_for_cert_install(self):
        server = SuccessfulProxyServer()

        with mock.patch.object(
            server,
            "ensure_ca_cert_installed",
            return_value="CA cert installed on TEST-UDID.",
        ) as ensure_cert:
            message = server.start(udid="TEST-UDID")

        ensure_cert.assert_called_once_with("TEST-UDID")
        self.assertIn("Proxy started on port 8080.", message)
        self.assertEqual(server.stop(), "Proxy stopped.")

    def test_start_raises_when_background_startup_fails(self):
        server = FailedProxyServer()

        with self.assertRaisesRegex(RuntimeError, "bind failed"):
            server.start(port=18081)

        self.assertFalse(server.is_running)

    def test_ensure_ca_cert_installed_checks_command_result(self):
        server = ProxyServer()

        with (
            mock.patch.object(server, "get_ca_cert_path", return_value="/tmp/mitmproxy-ca-cert.pem"),
            mock.patch("simulator_mcp.proxy.proxy_server.os.path.exists", return_value=True),
            mock.patch(
                "simulator_mcp.proxy.proxy_server.subprocess.run",
                return_value=CompletedProcess(
                    args=[],
                    returncode=1,
                    stdout="",
                    stderr="permission denied",
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                server.ensure_ca_cert_installed("TEST-UDID")

    def test_get_launch_env_rejects_incompatible_dylib_architecture(self):
        server = ProxyServer()

        with (
            mock.patch("simulator_mcp.proxy.proxy_server.os.path.exists", return_value=True),
            mock.patch("simulator_mcp.proxy.proxy_server.platform.machine", return_value="x86_64"),
            mock.patch(
                "simulator_mcp.proxy.proxy_server.subprocess.run",
                return_value=CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout="Non-fat file: libproxy_inject.dylib is architecture: arm64",
                    stderr="",
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "does not include x86_64"):
                server.get_launch_env()
