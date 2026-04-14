import time
import unittest
from subprocess import CompletedProcess
from unittest import mock

from simulator_mcp.proxy.mock_engine import MockEngine
from simulator_mcp.proxy.network_log import NetworkLog
from simulator_mcp.proxy.proxy_server import FrontmostApp, ProxyAddon, ProxyServer


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
        ):
            message = server.start(port=18080)

        self.assertIn("Proxy started on port 18080.", message)
        self.assertTrue(server.is_running)
        self.assertEqual(server.stop(), "Proxy stopped.")

    def test_start_local_mode_targets_frontmost_app_pid(self):
        server = SuccessfulProxyServer()

        with (
            mock.patch.object(
                server,
                "get_frontmost_app",
                return_value=FrontmostApp(
                    pid=10240,
                    bundle_id="com.webot.lite",
                    capture_pids=(10240, 10241),
                ),
            ),
            mock.patch.object(
                server,
                "ensure_ca_cert_installed",
                return_value="CA cert installed on TEST-UDID.",
            ) as ensure_cert,
        ):
            message = server.start(
                mode="local",
                udid="TEST-UDID",
                capture_frontmost_app=True,
            )

        ensure_cert.assert_called_once_with("TEST-UDID")
        self.assertEqual(server.mode, "local")
        self.assertEqual(
            server.local_target,
            FrontmostApp(
                pid=10240,
                bundle_id="com.webot.lite",
                capture_pids=(10240, 10241),
            ),
        )
        self.assertEqual(server._mode_spec, "local:10240,10241")
        self.assertIn("Capturing com.webot.lite (pids 10240, 10241)", message)
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

    def test_get_launch_env_rejects_local_mode(self):
        server = ProxyServer()
        server._mode = "local"

        with self.assertRaisesRegex(RuntimeError, "only available in regular mode"):
            server.get_launch_env()

    def test_get_frontmost_app_uses_latest_matching_event(self):
        server = ProxyServer()
        events = [
            {
                "timestamp": "2026-04-13 19:47:08.014536+0800",
                "eventMessage": (
                    "[coordinator] didAddExternalForegroundApplicationSceneHandle "
                    "pid:58264 scene:com.apple.frontboard.systemappservices/"
                    "FBSceneManager:sceneID%3Acom.apple.mobilesafari-"
                    "02E18A9B-55AE-42C4-86F7-C6A75C7884D4 now:<...>"
                ),
            },
            {
                "timestamp": "2026-04-13 19:58:36.804490+0800",
                "eventMessage": (
                    "[coordinator] didAddExternalForegroundApplicationSceneHandle "
                    "pid:10240 scene:com.apple.frontboard.systemappservices/"
                    "FBSceneManager:sceneID%3Acom.webot.lite-default now:<...>"
                ),
            },
        ]

        with (
            mock.patch.object(
                server,
                "_list_running_ui_apps",
                return_value={
                    58264: "com.apple.mobilesafari",
                    10240: "com.webot.lite",
                },
            ),
            mock.patch.object(
                server,
                "_get_related_process_pids",
                return_value=(10240, 12345),
            ),
            mock.patch.object(
                server,
                "_get_frontmost_scene_events",
                side_effect=[events, []],
            ),
        ):
            app = server.get_frontmost_app("TEST-UDID")

        self.assertEqual(app, FrontmostApp(
            pid=10240,
            bundle_id="com.webot.lite",
            timestamp="2026-04-13 19:58:36.804490+0800",
            capture_pids=(10240, 12345),
        ))

    def test_parse_frontmost_scene_event_falls_back_to_scene_bundle(self):
        server = ProxyServer()
        event = {
            "timestamp": "2026-04-13 19:58:36.804490+0800",
            "eventMessage": (
                "[coordinator] didAddExternalForegroundApplicationSceneHandle "
                "pid:10240 scene:com.apple.frontboard.systemappservices/"
                "FBSceneManager:sceneID%3Acom.webot.lite-default now:<...>"
            ),
        }

        app = server._parse_frontmost_scene_event(event, {})

        self.assertEqual(app, FrontmostApp(
            pid=10240,
            bundle_id="com.webot.lite",
            timestamp="2026-04-13 19:58:36.804490+0800",
        ))
