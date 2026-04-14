"""Device management MCP tools."""

from simulator_mcp.simulator import simctl


async def list_devices(arguments: dict) -> str:
    devices = await simctl.list_devices()
    if not devices:
        return "No simulators found."
    lines = []
    for d in devices:
        state = d.get("state", "Unknown")
        name = d.get("name", "Unknown")
        udid = d.get("udid", "")
        runtime = d.get("runtime", "").split(".")[-1] if d.get("runtime") else ""
        lines.append(f"  {name} ({runtime}) [{state}] {udid}")
    return "Simulators:\n" + "\n".join(lines)


async def boot_device(arguments: dict) -> str:
    udid = arguments["udid"]
    return await simctl.boot_device(udid)


async def shutdown_device(arguments: dict) -> str:
    udid = arguments["udid"]
    return await simctl.shutdown_device(udid)


async def install_app(arguments: dict) -> str:
    udid = arguments["udid"]
    app_path = arguments["app_path"]
    return await simctl.install_app(udid, app_path)


async def launch_app(arguments: dict) -> str:
    udid = arguments["udid"]
    bundle_id = arguments["bundle_id"]
    args = arguments.get("args")
    env = arguments.get("env") or {}
    use_proxy = arguments.get("proxy", False)
    cert_msg = None

    if use_proxy:
        from simulator_mcp.proxy.proxy_server import get_proxy_server
        proxy = get_proxy_server()
        if not proxy.is_running:
            raise RuntimeError("Proxy is not running. Call start_network_proxy first.")
        cert_msg = proxy.ensure_ca_cert_installed(udid)
        proxy_env = proxy.get_launch_env()
        env = {**env, **proxy_env}

    result = await simctl.launch_app(udid, bundle_id, args=args, env=env)
    if cert_msg:
        return f"{result} {cert_msg}"
    return result


async def open_url(arguments: dict) -> str:
    url = arguments["url"]
    udid = arguments.get("udid")
    if not udid:
        udid = await simctl.get_booted_device_udid()
        if not udid:
            raise RuntimeError("No booted simulator found.")
    return await simctl.open_url(udid, url)
