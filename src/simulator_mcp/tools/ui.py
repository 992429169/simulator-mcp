"""UI interaction MCP tools using fb-idb."""

from simulator_mcp.simulator import idb_client


async def tap(arguments: dict) -> str:
    udid = arguments["udid"]
    x = float(arguments["x"])
    y = float(arguments["y"])
    duration = arguments.get("duration")
    if duration is not None:
        duration = float(duration)
    await idb_client.tap(udid, x, y, duration=duration)
    return f"Tapped at ({x}, {y})"


async def swipe(arguments: dict) -> str:
    udid = arguments["udid"]
    start_x = float(arguments["start_x"])
    start_y = float(arguments["start_y"])
    end_x = float(arguments["end_x"])
    end_y = float(arguments["end_y"])
    duration = float(arguments.get("duration", 0.5))
    await idb_client.swipe(udid, (start_x, start_y), (end_x, end_y), duration=duration)
    return f"Swiped from ({start_x}, {start_y}) to ({end_x}, {end_y})"


async def input_text(arguments: dict) -> str:
    udid = arguments["udid"]
    text = arguments["text"]
    await idb_client.input_text(udid, text)
    return f"Input text: {text}"


async def press_button(arguments: dict) -> str:
    udid = arguments["udid"]
    button = arguments["button"]
    await idb_client.press_button(udid, button)
    return f"Pressed button: {button}"


async def get_ui_hierarchy(arguments: dict) -> str:
    udid = arguments["udid"]
    return await idb_client.get_accessibility(udid)


async def tap_element(arguments: dict) -> str:
    udid = arguments["udid"]
    text = arguments["text"]
    return await idb_client.tap_element(udid, text)
