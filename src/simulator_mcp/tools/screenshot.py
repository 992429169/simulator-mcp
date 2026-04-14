"""Screenshot MCP tool."""

import base64

from simulator_mcp.simulator import simctl


async def take_screenshot(arguments: dict) -> tuple[str, str]:
    """Take a screenshot and return (base64_png, mime_type).

    Returns a tuple so server.py can construct an ImageContent block.
    """
    udid = arguments.get("udid")
    if not udid:
        udid = await simctl.get_booted_device_udid()
        if not udid:
            raise RuntimeError("No booted simulator found.")
    png_bytes = await simctl.take_screenshot(udid)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return b64, "image/png"
