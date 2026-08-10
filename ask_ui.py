#!/usr/bin/env python3
"""Ask Gemini about a capture, in one dark card.

The card asks the question, spins while it waits, and shows the answer in the
same window — instead of a zenity prompt, a zenity progress bar and a separate
result window, which meant three windows in two themes for one interaction.

Usage: ask_ui.py <image> [preset question]
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

TITLE = "Ask Gemini"
DEFAULT_QUESTION = "What is this?"
MODEL = os.environ.get("LENS_ASK_MODEL", "gemini-3.6-flash-low")

# Off by default, and it should stay that way unless you want the trade.
#
# agy spends ~5s on network handshakes before it makes its first model call, so
# starting it while the lasso is still open hides most of that behind the time
# spent drawing — worth roughly 2-4s. The catch is that nothing then guarantees
# the crop is written before agy reads it: draw slowly and it reads a file that
# is not there yet. Asking agy to wait for the file instead is deterministic
# but costs far more than it saves — measured at 25s against 6s, because making
# it run a shell command adds several agent round trips.
PREWARM = os.environ.get("LENS_ASK_PREWARM") == "1"

# agy is an agentic coding CLI: left alone it narrates its tool use and signs
# off with a status report, neither of which belongs in a one-line answer.
PROMPT = """Look at the image file capture.png in this directory. {question}
Reply with the answer only. Do not mention the file, its path, what you did to
read it, task status, or next steps."""

CSS = b"""
window { background: transparent; }
.card {
  background-color: #202124;
  border: 1px solid #3c4043;
  border-radius: 14px;
  padding: 18px 20px;
}
.answer { color: #e8eaed; font-size: 16pt; }
.question { color: #9aa0a6; font-size: 11pt; margin-bottom: 10px; }
.footer { color: #9aa0a6; font-size: 10pt; margin-top: 14px; }
.working { color: #9aa0a6; font-size: 13pt; }
entry {
  background-color: #303134;
  color: #e8eaed;
  border: 1px solid #5f6368;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 13pt;
}
entry:focus { border-color: #8ab4f8; }
"""


def agy_binary():
    local = os.path.expanduser("~/.local/bin/agy")
    return local if os.path.exists(local) else shutil.which("agy")


def ask_gemini(image, work=None):
    """Run one throwaway agy session and return its answer, or an error string.

    A fresh directory per ask matters: run them all from one path and they join
    a single Antigravity project, after which it answers from that
    conversation's memory of the previous image instead of reading this one.

    With `work` supplied the directory already exists and the image is being
    written into it concurrently — see PREWARM.
    """
    binary = agy_binary()
    if not binary:
        return None, "Antigravity CLI (agy) not found."

    own_dir = work is None
    work = work or tempfile.mkdtemp(prefix="lens-ask.")
    try:
        if own_dir:
            shutil.copyfile(image, os.path.join(work, "capture.png"))
        proc = subprocess.run(
            [binary, "-p", ask_gemini.prompt, "--new-project", "--model", MODEL,
             "--dangerously-skip-permissions", "--print-timeout", "3m"],
            cwd=work, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        )
        both = f"{proc.stderr or ''}\n{proc.stdout or ''}"
        if "authentication" in both.lower():
            return None, "Gemini is not signed in. Run:  lens login"
        # Quota is reported on stdout with a zero exit code, so it arrives
        # looking like an answer unless it is checked for.
        if "quota reached" in both.lower():
            resets = ""
            for word in both.split():
                if word.endswith("s.") and "h" in word and "m" in word:
                    resets = f" Resets in {word.rstrip('.')}"
                    break
            return None, ("Google AI quota used up for this period." + resets +
                          "\nSearch and Copy Text still work.")
        if proc.returncode != 0:
            return None, (proc.stderr or "agy failed").strip()[:400]
        answer = (proc.stdout or "").strip()
        if answer.startswith("Error:"):
            return None, answer[:400]
        return (answer, None) if answer else (None, "Gemini returned nothing.")
    except Exception as exc:  # noqa: BLE001 - surfaced in the card
        return None, str(exc)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def to_clipboard(text):
    """xclip on X11, wl-copy on Wayland — whichever is actually there."""
    for cmd in (["xclip", "-selection", "clipboard"], ["wl-copy"]):
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, input=text, text=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue


class Ask(Gtk.Application):
    def __init__(self, image, preset, inflight=None):
        # NON_UNIQUE, or a second ask hands off to the window already open and
        # never appears.
        super().__init__(application_id="dev.lens.ask",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.image = image
        self.preset = preset
        # (thread, result-dict) for a query already running from the prewarm
        self.inflight = inflight

    # --- window -----------------------------------------------------------

    def do_activate(self):
        self.win = Gtk.ApplicationWindow(application=self)
        self.win.set_title(TITLE)
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        # Placement happens from outside once the window exists, so keep it
        # invisible until it is where it belongs — otherwise it visibly jumps
        # from wherever the window manager first put it.
        self.win.set_opacity(0)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.add_css_class("card")
        self.win.set_child(self.card)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

        # Start asking straight away rather than waiting for the question to be
        # typed: agy needs ~6s regardless, so spending it while the user reads
        # a prompt box is pure dead time. A follow-up box appears with the
        # answer for when the default question is not the one they wanted.
        self.show_working(self.preset or DEFAULT_QUESTION)

        self.win.present()
        self.place_tries = 0
        GLib.timeout_add(120, self.place, True)

    def clear(self):
        while (child := self.card.get_first_child()) is not None:
            self.card.remove(child)

    def on_key(self, _c, keyval, *_a):
        if Gdk.keyval_name(keyval) == "Escape":
            self.win.close()
        return False

    # --- states -----------------------------------------------------------

    def show_prompt(self):
        self.clear()
        label = Gtk.Label(label="What do you want to know about this?")
        label.add_css_class("question")
        label.set_xalign(0)
        self.card.append(label)

        entry = Gtk.Entry()
        entry.set_text(DEFAULT_QUESTION)
        entry.set_width_chars(38)
        entry.connect("activate", lambda e: self.show_working(e.get_text().strip()))
        self.card.append(entry)
        entry.grab_focus()
        entry.select_region(0, -1)

        footer = Gtk.Label(label="Enter to ask · Esc to cancel")
        footer.add_css_class("footer")
        footer.set_xalign(0)
        self.card.append(footer)

    def show_working(self, question):
        self.clear()
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        spinner = Gtk.Spinner()
        spinner.start()
        row.append(spinner)
        label = Gtk.Label(label="Asking Gemini…")
        label.add_css_class("working")
        row.append(label)
        self.card.append(row)
        GLib.timeout_add(60, self.place, False)

        if self.inflight is not None:
            thread, result = self.inflight
            self.inflight = None  # follow-ups always start a fresh query
            threading.Thread(target=self.await_query, args=(thread, result),
                             daemon=True).start()
            return

        ask_gemini.prompt = PROMPT.format(question=question or DEFAULT_QUESTION)
        threading.Thread(target=self.run_query, daemon=True).start()

    def await_query(self, thread, result):
        """Wait on the query that started while the lasso was still open."""
        thread.join()
        answer, error = result.get("value", (None, "Gemini returned nothing."))
        GLib.idle_add(self.show_answer, answer, error)

    def run_query(self):
        answer, error = ask_gemini(self.image)
        GLib.idle_add(self.show_answer, answer, error)

    def show_answer(self, answer, error):
        self.clear()
        text = answer or error or "(no answer)"
        label = Gtk.Label(label=text)
        label.add_css_class("answer")
        label.set_wrap(True)
        label.set_selectable(True)
        label.set_xalign(0)
        label.set_max_width_chars(52)
        self.card.append(label)

        if answer:
            to_clipboard(answer)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Ask a follow-up…")
        entry.set_width_chars(38)
        entry.connect("activate", lambda e: self.show_working(e.get_text().strip()))
        self.card.append(entry)

        footer = Gtk.Label(
            label="Copied to clipboard · Esc to close" if answer else "Esc to close"
        )
        footer.add_css_class("footer")
        footer.set_xalign(0)
        self.card.append(footer)

        # A selectable label grabs focus and comes up fully selected, which
        # reads as a highlight rather than as text; parking focus in the
        # follow-up box keeps the answer looking like text.
        label.select_region(0, 0)
        entry.grab_focus()
        GLib.timeout_add(60, self.place, False)
        return False

    # --- placement --------------------------------------------------------

    def place(self, reveal):
        """Centre the card on screen, above everything else."""
        try:
            ids = subprocess.run(["xdotool", "search", "--name", f"^{TITLE}$"],
                                 capture_output=True, text=True).stdout.split()
            if not ids:
                # Not mapped yet — but give up eventually, because under
                # Wayland xdotool will never find it and the card would stay
                # invisible forever waiting to be placed.
                self.place_tries += 1
                if reveal and self.place_tries < 25:
                    return True
                if reveal:
                    self.win.set_opacity(1)
                return False
            wid = ids[-1]

            def shell(cmd):
                out = subprocess.run(cmd, capture_output=True, text=True).stdout
                return dict(line.split("=", 1)
                            for line in out.strip().splitlines() if "=" in line)

            geo = shell(["xdotool", "getwindowgeometry", "--shell", wid])
            w, h = int(geo.get("WIDTH", 400)), int(geo.get("HEIGHT", 160))

            # Centre on the monitor in use, not the combined desktop, or a
            # second screen puts the card across the seam.
            here = os.path.dirname(os.path.abspath(__file__))
            out = subprocess.run([sys.executable, os.path.join(here, "screen.py")],
                                 capture_output=True, text=True).stdout.split()
            mx, my, sw, sh = (int(v) for v in out[:4]) if len(out) >= 4 \
                else (0, 0, 1920, 1080)

            x = max(mx, mx + (sw - w) // 2)
            y = max(my, my + (sh - h) // 2)
            subprocess.run(["xdotool", "windowmove", wid, str(x), str(y)],
                           capture_output=True)
            subprocess.run(["wmctrl", "-i", "-r", wid, "-b", "add,above"],
                           capture_output=True)
        except Exception:
            pass
        if reveal:
            self.win.set_opacity(1)
        return False


def capture_with_prewarm(lasso, image, question):
    """Run the lasso, optionally with agy already starting up alongside it.

    Returns (work_dir or None, thread or None): a live query when prewarmed,
    nothing when the capture was cancelled.
    """
    work = None
    thread = None
    result = {}

    if PREWARM:
        work = tempfile.mkdtemp(prefix="lens-ask.")
        ask_gemini.prompt = PROMPT.format(question=question or DEFAULT_QUESTION)

        def run():
            result["value"] = ask_gemini(image, work=work)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()

    ok = subprocess.run([sys.executable, lasso, image]).returncode == 0
    if not ok:
        if work:
            shutil.rmtree(work, ignore_errors=True)
        return None

    if work:
        shutil.copyfile(image, os.path.join(work, "capture.png"))
    return work, thread, result


def main():
    args = sys.argv[1:]
    if not args:
        sys.exit("usage: ask_ui.py [--capture <lasso.py>] <image> [question]")

    inflight = None
    if args[0] == "--capture":
        if len(args) < 3:
            sys.exit("usage: ask_ui.py --capture <lasso.py> <image> [question]")
        lasso, image = args[1], args[2]
        preset = " ".join(args[3:]).strip()
        captured = capture_with_prewarm(lasso, image, preset)
        if captured is None:
            return  # cancelled, nothing to show
        work, thread, result = captured
        if thread is not None:
            inflight = (thread, result)
    else:
        image = args[0]
        preset = " ".join(args[1:]).strip()
    # Without this the window reports WM_CLASS "python3", which is far too
    # generic for the launcher to match on — it would claim every Python
    # window. Name it so the dock can pair it with lens-ask.desktop.
    GLib.set_prgname("lens-ask")
    Ask(image, preset, inflight).run([])


if __name__ == "__main__":
    main()
