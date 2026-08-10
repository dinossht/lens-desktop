# lens-desktop

Circle-to-search for the whole Linux desktop, not just what is inside a browser
tab.

Press a key, the screen freezes, you draw a shape around anything on it, and
Google Lens opens on that crop in a panel. Or copy the text out of it. Or ask
Gemini about it.

Google has no Lens app for Linux, and "Gemini in Chrome" is Mac, Windows and
ChromeOS only. Chrome's built-in Lens overlay exists on Linux but can only see
the current web page — not your PDF viewer, your video player, or a photo on
your desktop. This closes that gap.

## Hotkeys

| Key | What happens |
|-----|--------------|
| `Super+Shift+S` | Circle something → Google Lens panel |
| `Super+Shift+C` | Circle something → its text on the clipboard (Lens OCR) |
| `Super+Shift+T` | Circle something → its translation on the clipboard |
| `Super+Shift+A` | Circle something → ask Gemini about it |

The same four exist as launchers in the app grid, pinnable to the dock, and as
right-click actions on the main **Lens** icon.

Rough speeds: copying text is quickest, Lens next, and asking Gemini takes
around eight seconds — see [Why asking Gemini is slow](#why-asking-gemini-is-slow).

## Requirements

- **Linux with X11.** GNOME is what it is developed against. See
  [Wayland](#wayland) for the state of that.
- **Google Chrome** (or Chromium) — the Lens panel is a Chrome window.
- **Python 3** with GTK bindings, plus `flameshot`, `xclip`, `xdotool`,
  `wmctrl`, `zenity`.
- A **Google account**. Lens search, OCR and translation need nothing more than
  that — no sign-in at all, in fact. Only *Ask Gemini* needs a Google AI
  subscription; see below.

On Ubuntu/Debian:

```bash
sudo apt install flameshot xclip xdotool wmctrl zenity python3-gi \
                 gir1.2-gtk-3.0 gir1.2-gtk-4.0 libnotify-bin xdg-utils
```

## Install

```bash
git clone https://github.com/dinossht/lens-desktop.git
cd lens-desktop
./install.sh
```

That creates a Python virtualenv for the OCR library, installs the icons,
launchers and hotkeys, and links `lens` into `~/.local/bin`.

Try it with `Super+Shift+C`: circle some text on screen and paste it somewhere.
Nothing needs signing in for that.

### Setting up Ask Gemini

Only this one needs an account with a **Google AI subscription** (the free tier
works, with tighter limits).

Google retired the Gemini CLI's personal "Login with Google" on 2026-06-18 and
moved individual Pro/Ultra accounts to the **Antigravity CLI**, so that is what
this uses. `install.sh` installs it; you sign in once:

```bash
lens login      # or just: agy
```

It has to be a real terminal — the login prints a URL and waits for a pasted
code on stdin, which a hotkey has no way to provide. Complete the browser flow,
and `Super+Shift+A` works from then on.

If you run out of quota, the card says so and tells you when it resets. Search,
copy-text and translate keep working; they do not touch your subscription.

## Command line

```
lens                  # circle something → Lens panel
lens region           # rectangle drag instead of a lasso
lens full             # whole screen → Lens panel
lens text             # circle → OCR to clipboard
lens tr no            # circle → translate to Norwegian, to clipboard
lens ask "what is this?"
lens web              # circle → clipboard + open gemini.google.com to paste
lens login            # open a terminal to sign Gemini in
```

Every command also takes an image file instead of grabbing the screen:

```bash
lens ~/Pictures/part.png
lens text scan.png
lens ask "which connector is this?" photo.jpg
```

Environment variables: `LENS_PANEL_WIDTH`, `LENS_PANEL_HEIGHT`, `LENS_TR_LANG`
(default target language), `LENS_ASK_MODEL`, `LENS_ASK_PREWARM`.

## Privacy

Captures go to Google and nowhere else. That is worth stating because the other
community Lens tools for Linux relay screenshots through imgur or Litterbox to
get a public URL first; this does not.

The Lens panel runs from its own Chrome profile in `~/.local/share/lens-desktop`,
separate from your browsing.

## What this can and cannot be

There is **no mobile web Google Lens**. Send a phone user agent and Google
serves a "download the app" page with a QR code — the Android experience is the
native app and has no web equivalent, so no user-agent trick reaches it from
Linux. The panel therefore shows the *desktop* Lens web app, which still gives
you the image with a draggable selection box, the Search / Exact matches /
Visual matches tabs, an AI overview, a translate button and an ask-anything box.

The circle-to-search *gesture* — the part desktop Lens genuinely lacks — is
supplied locally by the lasso overlay.

## How it works

- **Lasso** (`lasso.py`) — grabs the screen, shows it fullscreen and dimmed, and
  lets you draw a free-form path; the crop is that path's bounding box. Esc or
  right-click cancels.
- **The panel** (`panel.py`) — a chromeless Chrome window from its own profile,
  centred and forced to a dark colour scheme, driven over the Chrome DevTools
  Protocol. Dressed as an application rather than a browser popup: `--class`
  pairs it with the desktop entry for the icon and name, `_MOTIF_WM_HINTS`
  strips the title bar, and `Esc` closes it.
- **OCR and translation** — [`chrome-lens-py`](https://pypi.org/project/chrome-lens-py/),
  which talks to Lens's own endpoint directly. No browser involved, which is why
  it is the fastest of the three.
- **Ask** (`ask_ui.py`) — one dark card that asks, spins and answers, driving
  the Antigravity CLI underneath.

### Why the upload is done the awkward way

Three approaches failed before the current one:

1. **`curl` the image to `lens.google.com/v3/upload`, open the results URL in
   the panel.** Google binds the session to the client that created it, so a
   different client gets `403`; once the user agents matched, a fresh cookieless
   profile loading a session it never created looks exactly like scraping and
   gets reCAPTCHA.
2. **Post from a local page of our own.** A cross-origin form POST from
   `file://` carries `Origin: null`, which Google bounces to a sign-in screen.
3. **Use a mobile user agent to get the phone UI.** It does not exist.

What works is letting Google's own page do the upload: load the Lens web app and
drop the file into its file input over CDP, which is indistinguishable from
picking a file by hand.

### Why asking Gemini is slow

About 6 of the ~8 seconds is the Antigravity CLI starting up, and almost none of
it is the model:

| | |
|---|---|
| `agy --version` (binary start) | 59ms |
| First model call | t+5.1s |
| Answer streamed back | t+5.8s |

The five seconds in between are sequential network round trips — token refresh,
`loadCodeAssist`, experiments, quota — repeated on every invocation, with no
flag to skip them and no daemon mode to reuse. Two things do help and are
already applied: `gemini-3.6-flash-low` (~14s → 6s versus the default model) and
`--new-project` (without it, agy indexes the working directory and the same
query takes ~40s).

`LENS_ASK_PREWARM=1` starts agy while the lasso is still open, hiding most of
that behind your drawing. It is **off by default** because nothing then
guarantees the crop is written before agy reads it — draw slowly and it reads a
file that is not there. Making agy wait for the file instead is deterministic
but measured 25s against 6s, because making it run a shell command adds several
agent round trips.

## Wayland

X11 only, in practice. The Wayland paths exist — portal screenshot, fullscreen
overlay instead of an override-redirect popup, `wl-copy` clipboard, placement
that fails soft — but two things get in the way, and they are policy rather than
bugs:

- GNOME refuses `org.gnome.Shell.Screenshot` to unsandboxed callers
  ("Screenshot is not allowed") and denies a *non-interactive* portal
  screenshot, so every capture needs a confirmation through GNOME's own
  screenshot UI.
- Window placement has no Wayland equivalent, so the panel and the answer card
  land wherever the compositor puts them.

Untested — I have no Wayland session to try it in. Patches welcome.

## Gotchas worth knowing

Collected while building this; each one cost time.

- The Lens page carries **three** `input[type=file]` elements and only the one
  named `encoded_image` is Lens's. The decoys render first, so a selector that
  falls back early uploads into one of them and the picker just sits there.
- Running every `agy` call from one directory puts them in a single Antigravity
  project, and once it has history it answers about the *previous* image rather
  than reading the new one — even though the file on disk changed. Each ask gets
  a throwaway directory and `--new-project`.
- Chrome's `--window-size` is in *logical* pixels while xdotool counts *device*
  pixels, so on a HiDPI screen asking for a width gets you double.
- Chrome re-decorates itself while starting, so setting `_MOTIF_WM_HINTS` once
  is not enough; the frame comes back a moment later.
- `window.close()` is refused for a window the page did not open, so `Esc` needs
  a watcher holding the DevTools connection.
- `Gtk.Application` with an `application_id` is single-instance: without
  `NON_UNIQUE` a second answer hands off to the first window and never appears.
- A selectable `Gtk.Label` comes up with all its text selected, which reads as a
  highlight; clear it with `select_region(0, 0)`.
- GTK4 has no window positioning: place from outside once the window exists, and
  keep it at opacity 0 until then or it visibly jumps.
- `xclip` stays resident to own the selection, so redirect its stdio or it holds
  the caller's pipes open and anything reading the script's output hangs.

## Licence

MIT.
