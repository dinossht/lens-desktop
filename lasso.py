#!/usr/bin/env python3
"""Circle-to-search style lasso overlay.

Freezes the screen, lets you draw any shape around something, and writes the
crop of that shape's bounding box to the path given as argv[1].

Exit 0 on a selection, 1 if cancelled (Esc, right-click, or a stray click).
"""
import os
import sys
import tempfile

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

MIN_SIZE = 12  # a drag smaller than this is a misclick, not a selection
WAYLAND = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def grab_screen(width, height):
    """Return a pixbuf of the whole screen.

    Wayland forbids reading the root window, so ask the desktop portal instead.
    """
    if not WAYLAND:
        root = Gdk.get_default_root_window()
        shot = Gdk.pixbuf_get_from_window(root, 0, 0, width, height)
        if shot is not None:
            return shot

    # GNOME denies a silent full-screen grab (portal response 2), so fall back
    # to the interactive portal, where GNOME's own screenshot UI confirms the
    # capture. The lasso then runs over the image it returns, so the circling
    # still works — it just costs one extra confirmation.
    return portal_screenshot(interactive=False) or portal_screenshot(interactive=True)


def portal_screenshot(interactive=False):
    """Screenshot through the XDG desktop portal.

    GNOME refuses org.gnome.Shell.Screenshot for unsandboxed callers
    ("Screenshot is not allowed"), so the portal is the only route left on
    Wayland.
    """
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    token = f"lens{os.getpid()}{'i' if interactive else ''}"
    loop = GLib.MainLoop()
    out = {}

    sender = bus.get_unique_name()[1:].replace(".", "_")
    request_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"

    def on_response(_conn, _sender, _path, _iface, _signal, params, *_user):
        out["code"], out["results"] = params.unpack()
        loop.quit()

    bus.signal_subscribe(
        "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request",
        "Response", request_path, None, Gio.DBusSignalFlags.NONE, on_response, None,
    )
    bus.call_sync(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
        "org.freedesktop.portal.Screenshot", "Screenshot",
        GLib.Variant("(sa{sv})", ("", {
            "handle_token": GLib.Variant("s", token),
            "interactive": GLib.Variant("b", interactive),
        })),
        GLib.VariantType("(o)"), Gio.DBusCallFlags.NONE, 15000, None,
    )

    GLib.timeout_add_seconds(20, lambda: (loop.quit(), False)[1])
    loop.run()

    if out.get("code") != 0:
        return None
    uri = out.get("results", {}).get("uri")
    if not uri:
        return None
    path = GLib.filename_from_uri(uri)[0]
    try:
        return GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


class Lasso(Gtk.Window):
    def __init__(self, out_path):
        # An override-redirect popup is what makes this feel instant on X11,
        # but Wayland will not place one, so there it has to be an ordinary
        # fullscreen window.
        super().__init__(
            type=Gtk.WindowType.TOPLEVEL if WAYLAND else Gtk.WindowType.POPUP
        )
        self.out_path = out_path
        self.points = []
        self.drawing = False
        self.ok = False

        # Windows are placed in logical pixels but the screen grab comes back
        # in device pixels, and on a HiDPI display those differ by the scale
        # factor — asking the root window for its size gives device pixels and
        # makes the overlay twice too big. Take the size from the monitors.
        display = Gdk.Display.get_default()
        self.logical_w = self.logical_h = 0
        for i in range(display.get_n_monitors()):
            geo = display.get_monitor(i).get_geometry()
            self.logical_w = max(self.logical_w, geo.x + geo.width)
            self.logical_h = max(self.logical_h, geo.y + geo.height)

        self.shot = grab_screen(self.logical_w, self.logical_h)
        if self.shot is None:
            sys.exit("could not grab the screen")

        self.set_app_paintable(True)
        self.set_keep_above(True)
        self.set_decorated(False)
        if WAYLAND:
            self.fullscreen()
        else:
            self.move(0, 0)
            self.resize(self.logical_w, self.logical_h)

        self.area = Gtk.DrawingArea()
        self.area.connect("draw", self.on_draw)
        self.add(self.area)

        self.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
        )
        self.connect("button-press-event", self.on_press)
        self.connect("motion-notify-event", self.on_motion)
        self.connect("button-release-event", self.on_release)
        self.connect("key-press-event", self.on_key)

    # The window is sized in logical pixels while the grab is in device
    # pixels; on a scaled display those differ, so map between them.
    def scale(self):
        w = self.get_allocated_width() or 1
        return self.shot.get_width() / w

    def on_draw(self, _widget, cr):
        w = self.get_allocated_width()
        h = self.get_allocated_height()

        scaled = self.shot.scale_simple(w, h, GdkPixbuf.InterpType.BILINEAR)
        Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
        cr.paint()

        # Dim everything, then punch the circled area back to full brightness.
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.paint()

        if len(self.points) > 1:
            cr.save()
            self.trace(cr)
            cr.close_path()
            cr.clip()
            Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
            cr.paint()
            cr.restore()

            self.trace(cr)
            cr.close_path()
            cr.set_source_rgba(0.55, 0.75, 1.0, 0.95)
            cr.set_line_width(4)
            cr.set_line_join(1)  # round
            cr.set_line_cap(1)
            cr.stroke()
        return False

    def trace(self, cr):
        cr.move_to(*self.points[0])
        for pt in self.points[1:]:
            cr.line_to(*pt)

    def on_press(self, _w, event):
        if event.button != 1:
            Gtk.main_quit()
            return True
        self.drawing = True
        self.points = [(event.x, event.y)]
        return True

    def on_motion(self, _w, event):
        if self.drawing:
            self.points.append((event.x, event.y))
            self.queue_draw()
        return True

    def on_release(self, _w, event):
        if event.button != 1 or not self.drawing:
            return True
        self.drawing = False
        self.finish()
        return True

    def on_key(self, _w, event):
        if Gdk.keyval_name(event.keyval) == "Escape":
            Gtk.main_quit()
        return True

    def finish(self):
        if len(self.points) < 3:
            Gtk.main_quit()
            return
        s = self.scale()
        xs = [p[0] * s for p in self.points]
        ys = [p[1] * s for p in self.points]
        x0, y0 = int(min(xs)), int(min(ys))
        x1, y1 = int(max(xs)), int(max(ys))
        w, h = x1 - x0, y1 - y0
        if w < MIN_SIZE or h < MIN_SIZE:
            Gtk.main_quit()
            return

        x0 = max(0, x0)
        y0 = max(0, y0)
        w = min(w, self.shot.get_width() - x0)
        h = min(h, self.shot.get_height() - y0)

        crop = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        self.shot.copy_area(x0, y0, w, h, crop, 0, 0)
        crop.savev(self.out_path, "png", [], [])
        self.ok = True
        Gtk.main_quit()


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: lasso.py <output.png>")
    win = Lasso(sys.argv[1])
    win.show_all()
    # Grab input so the drag belongs to the overlay and not to whatever is
    # underneath it.
    win.get_window().set_cursor(Gdk.Cursor.new_from_name(win.get_display(), "crosshair"))
    # Wayland has no global input grab; a focused fullscreen window already
    # receives everything, so only X11 needs this.
    if not WAYLAND:
        seat = win.get_display().get_default_seat()
        seat.grab(win.get_window(), Gdk.SeatCapabilities.ALL, True, None, None, None)
    Gtk.main()
    sys.exit(0 if win.ok else 1)


if __name__ == "__main__":
    main()
