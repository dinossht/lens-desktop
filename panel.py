#!/usr/bin/env python3
"""Open the Lens panel and hand it the screenshot.

Google will not accept a cross-origin POST from a page of ours — an Origin of
`null` gets bounced to a sign-in screen — so the upload has to be performed by
Google's own page. This drives it over the Chrome DevTools Protocol: load the
Lens web app and drop the file straight into its own file input, which is
indistinguishable from picking a file by hand.

The panel deliberately uses a desktop user agent. There is no mobile web Lens:
send a phone UA and Google serves a "download the app" page with a QR code, so
the desktop web app — image with a draggable selection box, Search/Text/
Translate tabs — is the closest thing that exists on Linux.

Usage: panel.py <image> [profile] [width] [height] [x] [y]
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

import websocket

DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)
LENS_URL = "https://lens.google.com/"
CHROME = "google-chrome"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class CDP:
    def __init__(self, ws_url):
        # Chrome rejects DevTools sockets that carry an Origin header unless it
        # was started with --remote-allow-origins; not sending one is tidier
        # than opening the endpoint up.
        self.ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
        self.n = 0

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


# Chrome refuses to let a page close a window it did not open, so window.close()
# is out. Instead the page just raises a flag and a detached watcher, which
# keeps the DevTools connection, closes the browser when it sees it. The flag is
# re-armed whenever it goes missing, which is what happens on navigation.
ESC_WATCH = """
(() => {
  if (!window.__lensEsc) {
    window.__lensEsc = 'armed';
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') window.__lensEsc = 'pressed';
    }, true);
  }
  return window.__lensEsc;
})()
"""


def style_window(x, y, w=None, h=None, timeout=20):
    """Make the Chrome window read as an app rather than a browser popup.

    Chrome already stamps WM_CLASS via --class, which is what pairs it with our
    .desktop entry for the icon and name; the rest is stripping the title bar
    and pinning it above other windows.
    """
    # Chrome re-decorates itself while it finishes starting up, so setting the
    # hint once is not enough — the frame comes back. Re-assert it for a few
    # seconds; geometry only needs doing once per window, and repeating that
    # would fight the user if they moved it.
    positioned = set()
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = subprocess.run(
            ["xdotool", "search", "--class", "lens-desktop"],
            capture_output=True, text=True,
        ).stdout.split()
        for wid in found:
            shell = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wid],
                capture_output=True, text=True,
            ).stdout
            geo = dict(
                line.split("=", 1) for line in shell.strip().splitlines() if "=" in line
            )
            # Chrome keeps small hidden helper windows on the same class.
            if int(geo.get("WIDTH", 0)) < 300 or int(geo.get("HEIGHT", 0)) < 300:
                continue
            subprocess.run(
                ["xprop", "-id", wid, "-f", "_MOTIF_WM_HINTS", "32c",
                 "-set", "_MOTIF_WM_HINTS", "0x2, 0x0, 0x0, 0x0, 0x0"],
                capture_output=True,
            )
            if wid in positioned:
                continue
            subprocess.run(["wmctrl", "-i", "-r", wid, "-b", "add,above"],
                           capture_output=True)
            # Set the size here as well as at launch: xdotool works in device
            # pixels, so this lands exactly where asked whatever the display
            # scaling, and dropping the frame shifts the window anyway.
            if w and h:
                subprocess.run(["xdotool", "windowsize", wid, str(w), str(h)],
                               capture_output=True)
            subprocess.run(["xdotool", "windowmove", wid, str(x), str(y)],
                           capture_output=True)
            positioned.add(wid)
        # Poll briskly: every tick here is a tick with the title bar on screen.
        time.sleep(0.1)
    return positioned


def page_target(port, timeout=30):
    """Wait for a page target that is not the devtools/extension noise."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2)
            for t in json.load(raw):
                if t.get("type") == "page" and not t["url"].startswith(
                    ("devtools://", "chrome-extension://")
                ):
                    return t
        except Exception:
            pass
        time.sleep(0.4)
    raise SystemExit("panel: chrome never produced a page target")


def find_file_inputs(cdp, timeout=25):
    """The Lens app builds its UI in JS, so poll until the inputs exist.

    The page carries three file inputs and only the one named `encoded_image`
    is Lens's — picking the first match uploads into the wrong one and nothing
    happens, so ask for that name first and fall back to trying them all.
    """
    def query(selector):
        # The page is still settling, so the document can be replaced between
        # fetching the root and querying it; that just means try again.
        try:
            root = cdp.send("DOM.getDocument", depth=-1, pierce=True)["root"]["nodeId"]
            return cdp.send(
                "DOM.querySelectorAll", nodeId=root, selector=selector
            ).get("nodeIds", [])
        except RuntimeError:
            return []

    # Hold out for the real input for as long as we can afford: the decoys
    # render first, so falling back early uploads into one of them and the
    # page just sits there.
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = query("input[name=encoded_image]")
        if found:
            return found
        time.sleep(0.5)
    return query("input[type=file]")


def watch_esc(port, browser_pid):
    """Poll the page's Escape flag and close the browser when it is set."""
    while True:
        try:
            target = page_target(port, timeout=5)
            cdp = CDP(target["webSocketDebuggerUrl"])
        except Exception:
            return  # the window is gone; nothing left to watch
        try:
            while True:
                state = cdp.send(
                    "Runtime.evaluate", expression=ESC_WATCH, returnByValue=True
                )["result"].get("value")
                if state == "pressed":
                    os.kill(browser_pid, signal.SIGTERM)
                    return
                time.sleep(0.25)
        except Exception:
            cdp.close()  # navigation dropped the session; reconnect and re-arm
            time.sleep(0.5)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--watch-esc":
        return watch_esc(int(sys.argv[2]), int(sys.argv[3]))

    if len(sys.argv) < 2:
        sys.exit("usage: panel.py <image> [profile] [w] [h] [x] [y]")
    image = os.path.abspath(sys.argv[1])
    profile = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser(
        "~/.local/share/lens-desktop/chrome-profile"
    )
    geom = sys.argv[3:8] or ["460", "900", "100", "40", "1"]
    w, h, x, y = (int(v) for v in geom[:4])
    # Device pixels in, logical pixels out: Chrome's own geometry flags are in
    # logical units, and xdotool corrects the result exactly afterwards.
    scale = max(1, int(geom[4]) if len(geom) > 4 else 1)

    port = free_port()
    proc = subprocess.Popen(
        [
            CHROME,
            f"--user-data-dir={profile}",
            f"--user-agent={DESKTOP_UA}",
            f"--app={LENS_URL}",
            f"--window-size={w // scale},{h // scale}",
            f"--window-position={x // scale},{y // scale}",
            "--class=lens-desktop",
            "--no-first-run",
            "--no-default-browser-check",
            # Google serves Search and Lens dark when the browser asks for a
            # dark colour scheme, which this profile always does regardless of
            # the desktop theme.
            "--force-dark-mode",
            "--enable-features=WebContentsForceDark",
            f"--remote-debugging-port={port}",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(proc.pid)
    sys.stdout.flush()

    # Strip the frame the instant the window exists. Chrome has no
    # undecorated-window flag on Linux, so the title bar is always drawn first
    # and removed after; waiting for DevTools to answer first left it up for a
    # quarter of a second, so watch for the window on a thread instead.
    threading.Thread(target=style_window, args=(x, y, w, h, 8), daemon=True).start()

    target = page_target(port)
    cdp = CDP(target["webSocketDebuggerUrl"])
    try:
        cdp.send("Page.enable")
        cdp.send("DOM.enable")

        node_ids = find_file_inputs(cdp)
        if not node_ids:
            raise SystemExit("panel: no file input on the Lens page")

        for node_id in node_ids:
            try:
                cdp.send("DOM.setFileInputFiles", files=[image], nodeId=node_id)
            except RuntimeError:
                continue

        # Confirm Lens actually took it rather than leaving the picker up.
        deadline = time.time() + 25
        while time.time() < deadline:
            for t in json.load(
                urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2)
            ):
                if t.get("type") == "page" and "vsrid" in t.get("url", ""):
                    # No styling call here: the thread started at launch is
                    # still re-asserting the frame, and style_window now runs
                    # to its deadline rather than returning early — calling it
                    # again would block for 20s before the Esc watcher starts,
                    # which is exactly when Esc gets pressed.
                    subprocess.Popen(
                        [sys.executable, os.path.abspath(__file__),
                         "--watch-esc", str(port), str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    return
            time.sleep(1)
        raise SystemExit("panel: upload did not produce Lens results")
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
