from __future__ import annotations

import os
import sys
import webbrowser


def get_browser_name_fallback() -> str | None:
    try:
        controller = webbrowser.get()
        return getattr(controller, "name", None)
    except Exception:
        return None


def should_attempt_browser_launch() -> bool:
    browser_blocklist = ["www-browser", "lynx", "links", "w3m", "elinks", "links2"]
    browser_env = os.environ.get("BROWSER") or get_browser_name_fallback()
    if browser_env and browser_env in browser_blocklist:
        return False

    if os.environ.get("CI") or os.environ.get("DEBIAN_FRONTEND") == "noninteractive":
        return False

    is_ssh = bool(os.environ.get("SSH_CONNECTION"))

    if sys.platform == "linux":
        display_variables = ["DISPLAY", "WAYLAND_DISPLAY", "MIR_SOCKET"]
        has_display = any(os.environ.get(v) for v in display_variables)
        if not has_display:
            return False

    if is_ssh and sys.platform != "linux":
        return False

    return True
