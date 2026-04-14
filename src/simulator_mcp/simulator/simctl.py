"""Wrapper around xcrun simctl commands."""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)


async def run_simctl(*args: str, timeout: float = 30.0) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        "xcrun", "simctl", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(
            f"simctl {' '.join(args)} failed (rc={proc.returncode}): {stderr.decode()}"
        )
    return stdout


async def list_devices() -> list[dict]:
    raw = await run_simctl("list", "devices", "-j")
    data = json.loads(raw)
    devices = []
    for runtime, device_list in data.get("devices", {}).items():
        for d in device_list:
            d["runtime"] = runtime
            devices.append(d)
    return devices


async def boot_device(udid: str) -> str:
    await run_simctl("boot", udid)
    return f"Device {udid} booted."


async def shutdown_device(udid: str) -> str:
    await run_simctl("shutdown", udid)
    return f"Device {udid} shut down."


async def install_app(udid: str, app_path: str) -> str:
    await run_simctl("install", udid, app_path)
    return f"App installed on {udid} from {app_path}."


async def launch_app(
    udid: str,
    bundle_id: str,
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    cmd = ["launch", udid, bundle_id]
    if args:
        cmd.extend(args)

    # SIMCTL_CHILD_* env vars must be set as process environment variables,
    # not as simctl command arguments. simctl passes them to the launched app.
    proc_env = None
    if env:
        import os
        proc_env = os.environ.copy()
        for k, v in env.items():
            key = k if k.startswith("SIMCTL_CHILD_") else f"SIMCTL_CHILD_{k}"
            proc_env[key] = v

    proc = await asyncio.create_subprocess_exec(
        "xcrun", "simctl", *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=proc_env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    if proc.returncode != 0:
        raise RuntimeError(f"simctl launch failed: {stderr.decode()}")
    return f"Launched {bundle_id} on {udid}."


async def take_screenshot(udid: str) -> bytes:
    """Capture screenshot as PNG bytes."""
    proc = await asyncio.create_subprocess_exec(
        "xcrun", "simctl", "io", udid, "screenshot", "-",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
    if proc.returncode != 0:
        raise RuntimeError(f"Screenshot failed: {stderr.decode()}")
    return stdout


async def open_url(udid: str, url: str) -> str:
    await run_simctl("openurl", udid, url)
    return f"Opened URL: {url}"


async def pbcopy(udid: str, text: str) -> None:
    """Write text to simulator clipboard via simctl pbcopy."""
    proc = await asyncio.create_subprocess_exec(
        "xcrun", "simctl", "pbcopy", udid,
        stdin=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(
        proc.communicate(input=text.encode("utf-8")), timeout=10.0
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pbcopy failed: {stderr.decode()}")


async def install_ca_cert(udid: str, cert_path: str) -> str:
    await run_simctl("keychain", udid, "add-root-cert", cert_path)
    return f"CA certificate installed on {udid}."


async def get_booted_device_udid() -> str | None:
    devices = await list_devices()
    for d in devices:
        if d.get("state") == "Booted":
            return d["udid"]
    return None
