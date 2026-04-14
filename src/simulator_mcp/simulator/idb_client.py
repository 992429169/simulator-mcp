"""fb-idb client wrapper for UI interaction."""

import json
import logging
import shutil

from idb.common.types import HIDButtonType
from idb.grpc.management import ClientManager

logger = logging.getLogger(__name__)

_manager: ClientManager | None = None


def _get_manager() -> ClientManager:
    global _manager
    if _manager is None:
        companion_path = shutil.which("idb_companion")
        _manager = ClientManager(companion_path=companion_path, logger=logger)
    return _manager


async def tap(udid: str, x: float, y: float, duration: float | None = None):
    async with _get_manager().from_udid(udid=udid) as client:
        await client.tap(x=x, y=y, duration=duration)


async def swipe(udid: str, start: tuple, end: tuple, duration: float = 0.5):
    async with _get_manager().from_udid(udid=udid) as client:
        await client.swipe(p_start=start, p_end=end, duration=duration)


async def input_text(udid: str, text: str):
    async with _get_manager().from_udid(udid=udid) as client:
        await client.text(text)


async def press_button(udid: str, button: str):
    btn_map = {
        "home": HIDButtonType.HOME,
        "lock": HIDButtonType.LOCK,
        "siri": HIDButtonType.SIRI,
    }
    button_lower = button.lower()
    if button_lower not in btn_map:
        raise ValueError(f"Unknown button: {button}. Supported: {list(btn_map.keys())}")
    async with _get_manager().from_udid(udid=udid) as client:
        await client.button(btn_map[button_lower])


async def get_accessibility(udid: str) -> str:
    async with _get_manager().from_udid(udid=udid) as client:
        info = await client.accessibility_info(point=None, nested=True)
        return info.json


def _find_element(node: dict, text: str) -> dict | None:
    """Recursively find an element by label or title match."""
    label = node.get("AXLabel", "") or ""
    title = node.get("AXValue", "") or ""
    if text in label or text in title:
        return node
    for child in node.get("children", []):
        found = _find_element(child, text)
        if found:
            return found
    return None


def _element_center(element: dict) -> tuple[float, float]:
    """Get center coordinates from element frame."""
    frame = element.get("frame", element.get("AXFrame", {}))
    x = frame.get("x", 0)
    y = frame.get("y", 0)
    w = frame.get("width", frame.get("w", 0))
    h = frame.get("height", frame.get("h", 0))
    return (x + w / 2, y + h / 2)


async def tap_element(udid: str, text: str) -> str:
    """Find element by text and tap its center."""
    raw = await get_accessibility(udid)
    tree = json.loads(raw) if isinstance(raw, str) else raw
    # tree may be a list of root nodes
    roots = tree if isinstance(tree, list) else [tree]
    for root in roots:
        element = _find_element(root, text)
        if element:
            cx, cy = _element_center(element)
            await tap(udid, cx, cy)
            return f"Tapped element '{text}' at ({cx}, {cy})"
    raise ValueError(f"Element with text '{text}' not found in UI hierarchy")
