"""mitmproxy proxy server for simulator traffic interception."""

import asyncio
import json
import logging
import os
import platform
import subprocess
import threading
import time

from simulator_mcp.proxy.network_log import NetworkLog
from simulator_mcp.proxy.mock_engine import MockEngine

logger = logging.getLogger(__name__)
STARTUP_TIMEOUT_SECONDS = 5.0

# Path to the compiled proxy injection dylib
DYLIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "proxy-dylib", "libproxy_inject.dylib",
)


TEXT_BODY_CONTENT_TYPES = (
    "application/graphql",
    "application/javascript",
    "application/json",
    "application/problem+json",
    "application/x-javascript",
    "application/x-www-form-urlencoded",
    "application/xml",
    "application/xhtml+xml",
    "application/yaml",
)


def _decode_body(content: bytes | None, headers) -> str | None:
    if not content:
        return None

    content_type = (headers.get("content-type") or "").lower()
    is_text_body = content_type.startswith("text/") or any(
        token in content_type for token in TEXT_BODY_CONTENT_TYPES
    )
    if not is_text_body and b"\x00" in content:
        return "<binary>"

    try:
        return content.decode("utf-8", errors="replace")
    except Exception:
        return "<binary>"


class ProxyAddon:
    """mitmproxy addon for logging requests and applying mock rules."""

    def __init__(self, network_log: NetworkLog, mock_engine: MockEngine):
        self.network_log = network_log
        self.mock_engine = mock_engine
        self._start_times: dict[str, float] = {}
        self._mocked_flows: set[str] = set()

    def request(self, flow):
        self._start_times[flow.id] = time.time()

        mock = self.mock_engine.find_match(flow.request.pretty_url, flow.request.method)
        if mock:
            from mitmproxy import http
            flow.response = http.Response.make(
                mock.status_code,
                mock.get_response_body().encode(),
                dict(mock.response_headers),
            )
            self._mocked_flows.add(flow.id)
            logger.info(f"Mocked: {flow.request.method} {flow.request.pretty_url}")

    def _record_flow(self, flow, *, error: str | None = None):
        start = self._start_times.pop(flow.id, None)
        duration_ms = (time.time() - start) * 1000 if start else None
        mocked = flow.id in self._mocked_flows
        self._mocked_flows.discard(flow.id)

        req_body = _decode_body(flow.request.content, flow.request.headers)
        resp_body = _decode_body(
            flow.response.content if flow.response else None,
            flow.response.headers if flow.response else {},
        )

        self.network_log.add(
            method=flow.request.method,
            url=flow.request.pretty_url,
            status_code=flow.response.status_code if flow.response else None,
            error=error,
            request_headers=dict(flow.request.headers),
            response_headers=dict(flow.response.headers) if flow.response else {},
            request_body=req_body,
            response_body=resp_body,
            duration_ms=duration_ms,
            mocked=mocked,
        )

    def response(self, flow):
        self._record_flow(flow)

    def error(self, flow):
        message = None
        if getattr(flow, "error", None) is not None:
            message = str(flow.error)
        self._record_flow(flow, error=message or "unknown proxy error")


class StartupSignalAddon:
    """Signals when mitmproxy has finished binding its listening sockets."""

    def __init__(self, on_running):
        self._on_running = on_running

    def running(self):
        self._on_running()


class ProxyServer:
    """Manages mitmproxy in regular mode with DYLD injection for simulator apps.

    How it works:
    1. start() launches mitmproxy as a regular HTTP proxy on the specified port.
    2. It also installs the mitmproxy CA cert into the booted simulator's keychain
       so HTTPS interception works without certificate errors.
    3. When apps are launched via `launch_app` with proxy=True, the MCP server
       injects `DYLD_INSERT_LIBRARIES` pointing to libproxy_inject.dylib, which
       swizzles NSURLSessionConfiguration to route all traffic through the proxy.
    """

    def __init__(self):
        self.network_log = NetworkLog()
        self.mock_engine = MockEngine()
        self._master = None
        self._thread: threading.Thread | None = None
        self._port: int = 8080
        self._running = False
        self._state_lock = threading.Lock()
        self._startup_complete = threading.Event()
        self._startup_error: Exception | None = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def port(self) -> int:
        return self._port

    def start(
        self,
        port: int = 8080,
        udid: str | None = None,
    ) -> str:
        if self.is_running:
            return f"Proxy already running on port {self._port}."

        self._port = port
        self._startup_error = None
        self._startup_complete.clear()

        self._thread = threading.Thread(target=self._run_proxy, daemon=True)
        self._thread.start()

        if not self._startup_complete.wait(timeout=STARTUP_TIMEOUT_SECONDS):
            self.stop()
            raise RuntimeError(
                f"Proxy startup timed out after {STARTUP_TIMEOUT_SECONDS:.0f}s."
            )

        if self._startup_error is not None:
            error = self._startup_error
            thread = self._thread
            if thread and thread.is_alive():
                thread.join(timeout=1)
            raise RuntimeError(f"Proxy failed to start: {error}") from error

        if not self.is_running:
            raise RuntimeError("Proxy exited before startup completed.")

        cert_msg = self.ensure_ca_cert_installed(udid)
        return self._startup_message(cert_msg)

    def _run_proxy(self):
        loop = None
        try:
            from mitmproxy.options import Options
            from mitmproxy.tools.dump import DumpMaster

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            opts = Options(
                mode=["regular"],
                ssl_insecure=True,
                listen_host="0.0.0.0",
                listen_port=self._port,
                ignore_hosts=["^localhost$", "^127\\.0\\.0\\.1$", "^\\[::1\\]$"],
            )
            master = DumpMaster(
                opts,
                loop=loop,
                with_termlog=False,
                with_dumper=False,
            )
            addon = ProxyAddon(self.network_log, self.mock_engine)
            startup_signal = StartupSignalAddon(self._mark_started)
            master.addons.add(addon, startup_signal)
            with self._state_lock:
                self._master = master
            loop.run_until_complete(master.run())
        except Exception as exc:
            logger.exception("Proxy server error")
            self._mark_startup_failed(exc)
        finally:
            with self._state_lock:
                if not self._startup_complete.is_set() and self._startup_error is None:
                    self._startup_error = RuntimeError(
                        "Proxy exited during startup. Check mitmproxy logs for details."
                    )
                self._running = False
                self._master = None
                if self._thread is threading.current_thread():
                    self._thread = None
            self._startup_complete.set()
            if loop is not None:
                asyncio.set_event_loop(None)
                loop.close()

    def stop(self) -> str:
        with self._state_lock:
            master = self._master
            thread = self._thread
            was_running = self._running
            self._running = False

        if master:
            try:
                master.shutdown()
            except RuntimeError:
                logger.debug("Proxy event loop already closed during shutdown.", exc_info=True)

        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3)

        with self._state_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            if self._master is master and (thread is None or not thread.is_alive()):
                self._master = None

        if was_running or master is not None:
            return "Proxy stopped."
        return "Proxy is not running."

    def get_launch_env(self) -> dict[str, str]:
        """Return environment variables to inject into app launch for proxy interception."""
        if not os.path.exists(DYLIB_PATH):
            raise FileNotFoundError(
                f"Proxy injection dylib not found at {DYLIB_PATH}. "
                f"Build it with: cd proxy-dylib && xcrun -sdk iphonesimulator clang "
                f"-arch arm64 -dynamiclib -framework Foundation -framework CFNetwork "
                f"-o libproxy_inject.dylib proxy_inject.m"
            )
        self._check_dylib_arch_compatibility()
        return {
            "DYLD_INSERT_LIBRARIES": DYLIB_PATH,
            "PROXY_HOST": "127.0.0.1",
            "PROXY_PORT": str(self._port),
        }

    def ensure_ca_cert_installed(self, udid: str | None = None) -> str:
        cert_path = self.get_ca_cert_path()
        if not os.path.exists(cert_path):
            return f"CA cert not found ({cert_path})."

        target_udid = udid or self._get_booted_device_udid()
        if not target_udid:
            return "No booted simulator, skipped CA cert."

        result = subprocess.run(
            ["xcrun", "simctl", "keychain", target_udid, "add-root-cert", cert_path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            lowered = stderr.lower()
            if any(token in lowered for token in ("already", "exists", "duplicate")):
                return f"CA cert already installed on {target_udid}."
            raise RuntimeError(
                f"Failed to install CA cert on {target_udid}: {stderr or 'unknown error'}"
            )
        return f"CA cert installed on {target_udid}."

    def _get_booted_device_udid(self) -> str | None:
        result = subprocess.run(
            ["xcrun", "simctl", "list", "devices", "booted", "-j"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(f"Failed to list booted simulators: {stderr or 'unknown error'}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse simctl device list: {exc}") from exc

        for _runtime, devices in data.get("devices", {}).items():
            for device in devices:
                if device.get("state") == "Booted":
                    return device["udid"]
        return None

    def _startup_message(self, cert_msg: str) -> str:
        return (
            f"Proxy started on port {self._port}. {cert_msg} "
            f"Use launch_app with proxy=true to intercept app traffic."
        )

    def _mark_started(self) -> None:
        with self._state_lock:
            self._running = True
        self._startup_complete.set()

    def _mark_startup_failed(self, error: Exception) -> None:
        with self._state_lock:
            self._running = False
            self._startup_error = error
        self._startup_complete.set()

    def _check_dylib_arch_compatibility(self) -> None:
        current_arch = platform.machine().lower()
        if current_arch not in {"arm64", "x86_64"}:
            return

        try:
            result = subprocess.run(
                ["lipo", "-info", DYLIB_PATH],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return

        if result.returncode != 0:
            return

        arch_info = f"{result.stdout}\n{result.stderr}".lower()
        if current_arch in arch_info:
            return

        raise RuntimeError(
            f"Proxy injection dylib at {DYLIB_PATH} does not include {current_arch}. "
            "Rebuild it for your simulator architecture before using proxy=true."
        )

    def get_ca_cert_path(self) -> str:
        return os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")


# Singleton instance
_proxy_server: ProxyServer | None = None


def get_proxy_server() -> ProxyServer:
    global _proxy_server
    if _proxy_server is None:
        _proxy_server = ProxyServer()
    return _proxy_server
