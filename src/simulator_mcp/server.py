"""MCP server entry point with tool registration."""

import logging
import sys

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent

from simulator_mcp.tools import device, screenshot, network, ui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
)

app = Server("simulator-mcp")

TOOLS = [
    # Device management
    Tool(
        name="list_devices",
        description="List all iOS simulators with their state, UDID, and runtime.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="boot_device",
        description="Boot an iOS simulator by UDID.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
            },
            "required": ["udid"],
        },
    ),
    Tool(
        name="shutdown_device",
        description="Shutdown an iOS simulator by UDID.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
            },
            "required": ["udid"],
        },
    ),
    Tool(
        name="install_app",
        description="Install a .app bundle on an iOS simulator.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "app_path": {"type": "string", "description": "Path to .app bundle"},
            },
            "required": ["udid", "app_path"],
        },
    ),
    Tool(
        name="launch_app",
        description="Launch an app on an iOS simulator by bundle ID. Set proxy=true to intercept network traffic (requires start_network_proxy first).",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "bundle_id": {"type": "string", "description": "App bundle identifier"},
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Launch arguments",
                },
                "env": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Environment variables (will be prefixed with SIMCTL_CHILD_)",
                },
                "proxy": {
                    "type": "boolean",
                    "description": "If true, inject DYLD proxy to intercept all HTTP(S) traffic via mitmproxy.",
                },
            },
            "required": ["udid", "bundle_id"],
        },
    ),
    # Open URL (via simctl, no UI interaction needed)
    Tool(
        name="open_url",
        description="Open a URL or deep link in the simulator.",
        inputSchema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "URL or deep link to open"},
                "udid": {"type": "string", "description": "Device UDID (optional, uses booted device)"},
            },
            "required": ["url"],
        },
    ),
    # Screenshot
    Tool(
        name="take_screenshot",
        description="Take a screenshot of the simulator screen. Returns the image directly for visual analysis.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID (optional, uses booted device)"},
            },
        },
    ),
    # Network
    Tool(
        name="start_network_proxy",
        description="Start mitmproxy for intercepting simulator traffic. Use mode=local with capture_frontmost_app=true to target the current foreground simulator app by PID without relaunching it.",
        inputSchema={
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "Proxy port (default: 8080)"},
                "mode": {
                    "type": "string",
                    "enum": ["regular", "local"],
                    "description": "Proxy mode. regular uses launch_app(proxy=true); local uses macOS local capture.",
                },
                "udid": {
                    "type": "string",
                    "description": "Device UDID. Recommended for local mode when targeting the active simulator app.",
                },
                "target_pid": {
                    "type": "integer",
                    "description": "In local mode, capture only the specified host PID.",
                },
                "capture_frontmost_app": {
                    "type": "boolean",
                    "description": "In local mode, resolve the current frontmost simulator app and capture only that PID.",
                },
            },
        },
    ),
    Tool(
        name="stop_network_proxy",
        description="Stop the running network proxy.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="get_network_log",
        description="Get captured network requests/responses. Supports filtering by URL pattern and HTTP method.",
        inputSchema={
            "type": "object",
            "properties": {
                "url_pattern": {"type": "string", "description": "Filter by URL substring"},
                "method": {"type": "string", "description": "Filter by HTTP method (GET, POST, etc.)"},
                "limit": {"type": "integer", "description": "Max entries to return (default: 50)"},
            },
        },
    ),
    Tool(
        name="add_mock_rule",
        description="Add a mock rule to intercept matching requests and return a custom response.",
        inputSchema={
            "type": "object",
            "properties": {
                "url_pattern": {"type": "string", "description": "Regex pattern to match request URL"},
                "method": {"type": "string", "description": "HTTP method to match (optional, matches all if omitted)"},
                "status_code": {"type": "integer", "description": "Response status code (default: 200)"},
                "response_headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Response headers",
                },
                "response_body": {"type": "string", "description": "Response body string"},
                "response_body_file": {"type": "string", "description": "Path to a file whose content will be used as response body (for large payloads)"},
            },
            "required": ["url_pattern"],
        },
    ),
    Tool(
        name="remove_mock_rule",
        description="Remove a mock rule by its ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "Mock rule ID to remove"},
            },
            "required": ["rule_id"],
        },
    ),
    # UI interaction (via fb-idb)
    Tool(
        name="tap",
        description="Tap at iOS screen coordinates.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "x": {"type": "number", "description": "X coordinate (iOS points)"},
                "y": {"type": "number", "description": "Y coordinate (iOS points)"},
                "duration": {"type": "number", "description": "Long press duration in seconds (optional)"},
            },
            "required": ["udid", "x", "y"],
        },
    ),
    Tool(
        name="swipe",
        description="Swipe from one point to another on the iOS screen.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "start_x": {"type": "number", "description": "Start X coordinate"},
                "start_y": {"type": "number", "description": "Start Y coordinate"},
                "end_x": {"type": "number", "description": "End X coordinate"},
                "end_y": {"type": "number", "description": "End Y coordinate"},
                "duration": {"type": "number", "description": "Swipe duration in seconds (default: 0.5)"},
            },
            "required": ["udid", "start_x", "start_y", "end_x", "end_y"],
        },
    ),
    Tool(
        name="input_text",
        description="Type text into the currently focused input field on the simulator.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "text": {"type": "string", "description": "Text to input"},
            },
            "required": ["udid", "text"],
        },
    ),
    Tool(
        name="press_button",
        description="Press a hardware button on the simulator (home, lock, siri).",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "button": {
                    "type": "string",
                    "enum": ["home", "lock", "siri"],
                    "description": "Button to press",
                },
            },
            "required": ["udid", "button"],
        },
    ),
    Tool(
        name="get_ui_hierarchy",
        description="Get the accessibility tree (UI hierarchy) of the simulator screen as JSON. Useful for finding elements to interact with.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
            },
            "required": ["udid"],
        },
    ),
    Tool(
        name="tap_element",
        description="Find a UI element by its text/label and tap it. Searches the accessibility tree for a matching element and taps its center.",
        inputSchema={
            "type": "object",
            "properties": {
                "udid": {"type": "string", "description": "Device UDID"},
                "text": {"type": "string", "description": "Text or label to search for in the UI"},
            },
            "required": ["udid", "text"],
        },
    ),
]


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        match name:
            # Device management
            case "list_devices":
                result = await device.list_devices(arguments)
            case "boot_device":
                result = await device.boot_device(arguments)
            case "shutdown_device":
                result = await device.shutdown_device(arguments)
            case "install_app":
                result = await device.install_app(arguments)
            case "launch_app":
                result = await device.launch_app(arguments)
            # Open URL
            case "open_url":
                result = await device.open_url(arguments)
            # Screenshot
            case "take_screenshot":
                b64, mime = await screenshot.take_screenshot(arguments)
                return [ImageContent(type="image", data=b64, mimeType=mime)]
            # Network
            case "start_network_proxy":
                result = await network.start_network_proxy(arguments)
            case "stop_network_proxy":
                result = await network.stop_network_proxy(arguments)
            case "get_network_log":
                result = await network.get_network_log(arguments)
            case "add_mock_rule":
                result = await network.add_mock_rule(arguments)
            case "remove_mock_rule":
                result = await network.remove_mock_rule(arguments)
            # UI interaction
            case "tap":
                result = await ui.tap(arguments)
            case "swipe":
                result = await ui.swipe(arguments)
            case "input_text":
                result = await ui.input_text(arguments)
            case "press_button":
                result = await ui.press_button(arguments)
            case "get_ui_hierarchy":
                result = await ui.get_ui_hierarchy(arguments)
            case "tap_element":
                result = await ui.tap_element(arguments)
            case _:
                result = f"Unknown tool: {name}"

        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {e}")]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
