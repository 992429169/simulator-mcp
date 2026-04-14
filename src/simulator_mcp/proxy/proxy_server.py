"""mitmproxy proxy server with simulator-aware capture modes."""

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
from urllib.parse import unquote

from simulator_mcp.proxy.network_log import NetworkLog
from simulator_mcp.proxy.mock_engine import MockEngine

logger = logging.getLogger(__name__)
STARTUP_TIMEOUT_SECONDS = 5.0
LOG_QUERY_TIMEOUT_SECONDS = 20.0

# Path to the compiled proxy injection dylib
DYLIB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "proxy-dylib", "libproxy_inject.dylib",
)

FRONTMOST_SCENE_PREDICATE = (
    'process == "SpringBoard" AND '
    'eventMessage CONTAINS "didAddExternalForegroundApplicationSceneHandle"'
)
UIKIT_APP_SERVICE_RE = re.compile(
    r"^\s*(?P<pid>\d+)\s+.*UIKitApplication:(?P<bundle>[^\[]+)\[[^\]]+\]\[rb-legacy\]\s*$"
)
FRONTMOST_SCENE_EVENT_RE = re.compile(
    r"didAddExternalForegroundApplicationSceneHandle pid:(?P<pid>\d+) scene:(?P<scene>\S+)"
)
SCENE_BUNDLE_RE = re.compile(
    r"sceneID:(?P<bundle>.+?)(?:-default|-[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})$"
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
            logger.info(f"Mocked: {flow.request.method} {flow.request.pretty_url}")

    def _record_flow(self, flow, *, error: str | None = None):
        start = self._start_times.pop(flow.id, None)
        duration_ms = (time.time() - start) * 1000 if start else None

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


@dataclass(frozen=True)
class FrontmostApp:
    pid: int
    bundle_id: str
    timestamp: str | None = None
    capture_pids: tuple[int, ...] = ()


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
        self._mode: str = "regular"
        self._mode_spec: str = "regular"
        self._local_target: FrontmostApp | None = None
        self._running = False
        self._state_lock = threading.Lock()
        self._startup_complete = threading.Event()
        self._startup_error: Exception | None = None
        self._pid_monitor_stop: threading.Event | None = None
        self._pid_monitor_thread: threading.Thread | None = None
        self._local_udid: str | None = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def port(self) -> int:
        return self._port

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def local_target(self) -> FrontmostApp | None:
        return self._local_target

    def start(
        self,
        port: int = 8080,
        mode: str = "regular",
        udid: str | None = None,
        target_pid: int | None = None,
        capture_frontmost_app: bool = False,
    ) -> str:
        if self.is_running:
            if self._mode == "regular":
                return f"Proxy already running on port {self._port}."
            return "Proxy already running in local mode."

        normalized_mode = (mode or "regular").lower()
        if normalized_mode not in {"regular", "local"}:
            raise ValueError(f"Unsupported proxy mode: {mode}")
        if target_pid is not None and target_pid <= 0:
            raise ValueError("target_pid must be a positive integer.")
        if normalized_mode != "local" and (target_pid is not None or capture_frontmost_app):
            raise ValueError(
                "target_pid and capture_frontmost_app are only supported in local mode."
            )

        self._port = port
        self._mode = normalized_mode
        self._mode_spec = normalized_mode
        self._local_target = None
        self._local_udid = None
        self._startup_error = None
        self._startup_complete.clear()

        target_udid = udid
        if self._mode == "local":
            if capture_frontmost_app:
                target_udid = udid or self._get_booted_device_udid()
                if not target_udid:
                    raise RuntimeError(
                        "No booted simulator found. Boot a simulator or pass udid explicitly."
                    )
                self._local_target = self.get_frontmost_app(target_udid)
                target_pid = self._local_target.pid
            if self._local_target is not None and self._local_target.capture_pids:
                self._mode_spec = "local:" + ",".join(
                    str(pid) for pid in self._local_target.capture_pids
                )
            elif target_pid is not None:
                self._mode_spec = f"local:{target_pid}"
                if self._local_target is None:
                    self._local_target = FrontmostApp(
                        pid=target_pid,
                        bundle_id="unknown",
                        capture_pids=(target_pid,),
                    )

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

        cert_msg = self.ensure_ca_cert_installed(target_udid)
        self._local_udid = target_udid

        reset_count = 0
        if self._mode == "local" and self._local_target is not None:
            reset_count = self._reset_existing_connections(self._local_target.pid)
            self._start_pid_monitor()

        return self._startup_message(cert_msg, reset_count)

    def _run_proxy(self):
        loop = None
        try:
            from mitmproxy.options import Options
            from mitmproxy.tools.dump import DumpMaster

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            option_kwargs = {
                "mode": [self._mode_spec],
                "ssl_insecure": True,
            }
            if self._mode == "regular":
                option_kwargs.update(
                    {
                        "listen_host": "0.0.0.0",
                        "listen_port": self._port,
                        "ignore_hosts": ["^localhost$", "^127\\.0\\.0\\.1$", "^\\[::1\\]$"],
                    }
                )
            opts = Options(**option_kwargs)
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
        self._stop_pid_monitor()

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
        if self._mode != "regular":
            raise RuntimeError(
                "Launch-time proxy injection is only available in regular mode. "
                "Start the app first and use local PID capture instead."
            )
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

    def get_frontmost_app(self, udid: str) -> FrontmostApp:
        running_apps = self._list_running_ui_apps(udid)
        if not running_apps:
            raise RuntimeError(f"No running UIKit applications found on {udid}.")

        for window in ("30m", "24h"):
            for event in reversed(self._get_frontmost_scene_events(udid, window)):
                app = self._parse_frontmost_scene_event(event, running_apps)
                if app is not None:
                    capture_pids = self._get_related_process_pids(udid, app.bundle_id, app.pid)
                    if not capture_pids:
                        capture_pids = (app.pid,)
                    return FrontmostApp(
                        pid=app.pid,
                        bundle_id=app.bundle_id,
                        timestamp=app.timestamp,
                        capture_pids=capture_pids,
                    )

        raise RuntimeError(
            "Could not determine the frontmost simulator app. "
            "Bring the target app to the foreground and retry."
        )

    def _list_running_ui_apps(self, udid: str) -> dict[int, str]:
        result = subprocess.run(
            ["xcrun", "simctl", "spawn", udid, "launchctl", "print", f"user/{os.getuid()}"],
            capture_output=True,
            text=True,
            timeout=LOG_QUERY_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"Failed to list running simulator apps on {udid}: {stderr or 'unknown error'}"
            )

        apps: dict[int, str] = {}
        for line in result.stdout.splitlines():
            match = UIKIT_APP_SERVICE_RE.match(line)
            if not match:
                continue
            pid = int(match.group("pid"))
            if pid > 0:
                apps[pid] = match.group("bundle")
        return apps

    def _get_frontmost_scene_events(self, udid: str, window: str) -> list[dict]:
        result = subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "log",
                "show",
                "--style",
                "json",
                "--last",
                window,
                "--predicate",
                FRONTMOST_SCENE_PREDICATE,
            ],
            capture_output=True,
            text=True,
            timeout=LOG_QUERY_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                f"Failed to inspect simulator logs on {udid}: {stderr or 'unknown error'}"
            )

        output = result.stdout.strip()
        if not output:
            return []

        try:
            events = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Failed to parse simulator log output: {exc}") from exc

        if isinstance(events, dict):
            return [events]
        if isinstance(events, list):
            return events
        raise RuntimeError("Unexpected simulator log output format.")

    def _parse_frontmost_scene_event(
        self,
        event: dict,
        running_apps: dict[int, str],
    ) -> FrontmostApp | None:
        message = event.get("eventMessage") or ""
        match = FRONTMOST_SCENE_EVENT_RE.search(message)
        if not match:
            return None

        pid = int(match.group("pid"))
        bundle_id = running_apps.get(pid) or self._bundle_id_from_scene(match.group("scene"))
        if not bundle_id or bundle_id == "com.apple.springboard":
            return None

        return FrontmostApp(
            pid=pid,
            bundle_id=bundle_id,
            timestamp=event.get("timestamp"),
        )

    def _bundle_id_from_scene(self, scene: str) -> str | None:
        decoded_scene = unquote(scene)
        match = SCENE_BUNDLE_RE.search(decoded_scene)
        if not match:
            return None
        return match.group("bundle")

    def _get_related_process_pids(
        self,
        udid: str,
        bundle_id: str,
        app_pid: int,
    ) -> tuple[int, ...]:
        app_ref = f"[app<{bundle_id}((null))>:{app_pid}]"
        result = subprocess.run(
            [
                "xcrun",
                "simctl",
                "spawn",
                udid,
                "log",
                "show",
                "--style",
                "json",
                "--last",
                "5m",
                "--predicate",
                f'eventMessage CONTAINS "{app_ref}"',
            ],
            capture_output=True,
            text=True,
            timeout=LOG_QUERY_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            logger.warning("Failed to inspect helper process logs for %s", bundle_id)
            return (app_pid,)

        output = result.stdout.strip()
        if not output:
            return (app_pid,)

        try:
            events = json.loads(output)
        except json.JSONDecodeError:
            logger.warning("Failed to parse helper process logs for %s", bundle_id)
            return (app_pid,)

        if isinstance(events, dict):
            events = [events]
        if not isinstance(events, list):
            return (app_pid,)

        pids = {app_pid}
        for event in events:
            if not isinstance(event, dict):
                continue
            message = event.get("eventMessage") or ""
            if app_ref not in message:
                continue

            for match in re.finditer(r"PID=(\d+)", message):
                pids.add(int(match.group(1)))

            for match in re.finditer(r"WebContent\[(\d+)\]", message):
                pids.add(int(match.group(1)))

            for match in re.finditer(r"xpcservice<.+:(\d+)\]", message):
                pids.add(int(match.group(1)))

        return tuple(sorted(pids))

    def _start_pid_monitor(self) -> None:
        """Start background thread to detect app restart and re-attach proxy."""
        self._stop_pid_monitor()
        self._pid_monitor_stop = threading.Event()
        self._pid_monitor_thread = threading.Thread(
            target=self._monitor_pid, daemon=True
        )
        self._pid_monitor_thread.start()

    def _stop_pid_monitor(self) -> None:
        stop_event = self._pid_monitor_stop
        thread = self._pid_monitor_thread
        if stop_event:
            stop_event.set()
        if thread and thread.is_alive():
            thread.join(timeout=3)
        self._pid_monitor_stop = None
        self._pid_monitor_thread = None

    def _monitor_pid(self) -> None:
        """Watch for target app PID changes and re-attach proxy automatically."""
        stop = self._pid_monitor_stop
        if stop is None:
            return

        while not stop.wait(timeout=3):
            if not self.is_running:
                break
            target = self._local_target
            if target is None:
                break

            # Check if original PID is still alive
            try:
                os.kill(target.pid, 0)
                continue
            except ProcessLookupError:
                pass  # PID gone
            except PermissionError:
                continue  # Process exists

            # PID is gone — wait a moment for the app to restart
            if stop.wait(timeout=2):
                break

            try:
                udid = self._local_udid or self._get_booted_device_udid()
                if not udid:
                    continue

                new_app = self.get_frontmost_app(udid)
                if new_app.bundle_id != target.bundle_id:
                    continue

                new_mode_spec = "local:" + ",".join(
                    str(pid) for pid in new_app.capture_pids
                )

                master = self._master
                if master:
                    master.options.update(mode=[new_mode_spec])

                self._mode_spec = new_mode_spec
                self._local_target = new_app

                self._reset_existing_connections(new_app.pid)

                logger.info(
                    "Proxy re-attached to %s (new pids %s)",
                    new_app.bundle_id,
                    ", ".join(str(p) for p in new_app.capture_pids),
                )
            except Exception:
                logger.debug("PID monitor: re-attach failed", exc_info=True)

    def _reset_existing_connections(self, pid: int) -> int:
        """Shutdown existing non-local TCP connections to force re-establishment through proxy.

        In local mode, the redirector only captures new TCP connections. Connections
        established before the proxy started continue to bypass it. This method uses
        lldb to call shutdown() on those sockets, causing the app to reconnect through
        the proxy without requiring a full app restart.
        """
        try:
            external_fds = self._find_external_tcp_fds(pid)
        except Exception:
            logger.warning("Failed to list connections for pid %d", pid, exc_info=True)
            return 0

        if not external_fds:
            return 0

        try:
            return self._shutdown_fds_via_lldb(pid, external_fds)
        except Exception:
            logger.warning(
                "Failed to reset connections for pid %d", pid, exc_info=True
            )
            return 0

    def _find_external_tcp_fds(self, pid: int) -> list[int]:
        """Return deduplicated FDs for non-local ESTABLISHED TCP connections."""
        result = subprocess.run(
            ["lsof", "-iTCP", "-a", "-p", str(pid), "-sTCP:ESTABLISHED", "-Ffn"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        external_fds: list[int] = []
        current_fd: int | None = None
        for line in result.stdout.splitlines():
            if line.startswith("f"):
                try:
                    current_fd = int(line[1:])
                except ValueError:
                    current_fd = None
            elif line.startswith("n") and current_fd is not None:
                name = line[1:]
                if not any(
                    token in name
                    for token in ("127.0.0.1", "localhost", "::1", "[::1]")
                ):
                    external_fds.append(current_fd)
                current_fd = None

        return sorted(set(external_fds))

    def _shutdown_fds_via_lldb(self, pid: int, fds: list[int]) -> int:
        """Use lldb to call shutdown() on socket FDs in the target process."""
        # Build a single compound expression to minimize attach time.
        # shutdown(fd, SHUT_RDWR=2) gracefully closes the socket without
        # invalidating the FD, so the app detects the error and reconnects.
        expr_parts = [f"(int)shutdown({fd}, 2)" for fd in fds]
        compound_expr = "; ".join(expr_parts)

        result = subprocess.run(
            [
                "lldb",
                "-x",  # skip .lldbinit for speed
                "-p",
                str(pid),
                "--batch",
                "-o",
                "settings set target.preload-symbols false",
                "-o",
                f"expression {compound_expr}",
                "-o",
                "detach",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )

        if result.returncode != 0:
            logger.warning(
                "lldb returned %d for pid %d: %s",
                result.returncode,
                pid,
                result.stderr.strip()[:200],
            )
            return 0

        logger.info(
            "Reset %d existing connection(s) for pid %d (fds: %s)",
            len(fds),
            pid,
            ", ".join(str(fd) for fd in fds),
        )
        return len(fds)

    def _startup_message(self, cert_msg: str, reset_count: int = 0) -> str:
        if self._mode == "regular":
            return (
                f"Proxy started on port {self._port}. {cert_msg} "
                f"Use launch_app with proxy=true to intercept app traffic."
            )

        reset_msg = ""
        if reset_count > 0:
            reset_msg = f" Reset {reset_count} existing connection(s) to force re-establishment through proxy."

        if self._local_target is not None and self._local_target.bundle_id != "unknown":
            pid_summary = ", ".join(str(pid) for pid in self._local_target.capture_pids)
            return (
                "Proxy started in local mode. "
                f"{cert_msg} Capturing {self._local_target.bundle_id} "
                f"(pids {pid_summary}) without relaunching the app.{reset_msg}"
            )

        if self._local_target is not None:
            return (
                "Proxy started in local mode. "
                f"{cert_msg} Capturing pid {self._local_target.pid} without relaunching the app.{reset_msg}"
            )

        return f"Proxy started in local mode. {cert_msg}{reset_msg}"

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
