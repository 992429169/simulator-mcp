"""Network proxy MCP tools."""

import json

from simulator_mcp.proxy.proxy_server import get_proxy_server


async def start_network_proxy(arguments: dict) -> str:
    port = arguments.get("port", 8080)
    mode = arguments.get("mode", "regular")
    udid = arguments.get("udid")
    target_pid = arguments.get("target_pid")
    capture_frontmost_app = arguments.get("capture_frontmost_app", False)
    proxy = get_proxy_server()
    return proxy.start(
        port=port,
        mode=mode,
        udid=udid,
        target_pid=target_pid,
        capture_frontmost_app=capture_frontmost_app,
    )


async def stop_network_proxy(arguments: dict) -> str:
    proxy = get_proxy_server()
    return proxy.stop()


async def get_network_log(arguments: dict) -> str:
    proxy = get_proxy_server()
    url_pattern = arguments.get("url_pattern")
    method = arguments.get("method")
    limit = arguments.get("limit", 50)
    entries = proxy.network_log.query(
        url_pattern=url_pattern, method=method, limit=limit
    )
    if not entries:
        return "No matching network log entries."
    return json.dumps(entries, indent=2, ensure_ascii=False)


async def add_mock_rule(arguments: dict) -> str:
    proxy = get_proxy_server()
    rule = proxy.mock_engine.add_rule(
        url_pattern=arguments["url_pattern"],
        method=arguments.get("method"),
        status_code=arguments.get("status_code", 200),
        response_headers=arguments.get("response_headers"),
        response_body=arguments.get("response_body", ""),
        response_body_file=arguments.get("response_body_file"),
    )
    return json.dumps(rule.to_dict(), indent=2, ensure_ascii=False)


async def remove_mock_rule(arguments: dict) -> str:
    proxy = get_proxy_server()
    rule_id = arguments["rule_id"]
    if proxy.mock_engine.remove_rule(rule_id):
        return f"Rule {rule_id} removed."
    return f"Rule {rule_id} not found."
