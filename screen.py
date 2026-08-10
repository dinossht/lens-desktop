#!/usr/bin/env python3
"""Report the monitor to place windows on, in device pixels.

Prints: X Y WIDTH HEIGHT SCALE

Everything that positions a window needs the same answer to "which screen, and
how big is it really", and it has to be the monitor the user is actually
looking at rather than the whole desktop — otherwise a second screen puts
centred windows across the seam. Falls back to the primary monitor when the
pointer cannot be located, which is the case on Wayland.
"""
import subprocess
import sys

import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk  # noqa: E402


def pointer():
    """Pointer position in device pixels, or None if it cannot be had."""
    try:
        out = subprocess.run(["xdotool", "getmouselocation", "--shell"],
                             capture_output=True, text=True, timeout=2).stdout
        loc = dict(line.split("=", 1) for line in out.strip().splitlines()
                   if "=" in line)
        return int(loc["X"]), int(loc["Y"])
    except Exception:
        return None


def main():
    display = Gdk.Display.get_default()
    if display is None:
        print("0 0 1920 1080 1")
        return

    monitors = [display.get_monitor(i) for i in range(display.get_n_monitors())]
    monitors = [m for m in monitors if m is not None]
    if not monitors:
        print("0 0 1920 1080 1")
        return

    chosen = None
    at = pointer()
    if at is not None:
        px, py = at
        for m in monitors:
            g = m.get_geometry()
            s = m.get_scale_factor() or 1
            # get_geometry() is logical; the pointer is in device pixels.
            if (g.x * s <= px < (g.x + g.width) * s
                    and g.y * s <= py < (g.y + g.height) * s):
                chosen = m
                break
    if chosen is None:
        chosen = next((m for m in monitors if m.is_primary()), monitors[0])

    g = chosen.get_geometry()
    s = chosen.get_scale_factor() or 1
    print(f"{g.x * s} {g.y * s} {g.width * s} {g.height * s} {s}")


if __name__ == "__main__":
    sys.exit(main())
