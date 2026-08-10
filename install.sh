#!/usr/bin/env bash
# Installs lens-desktop: dependencies, the venv used for OCR, the Gemini CLI,
# the ~/.local/bin/lens symlink and the GNOME hotkeys. Safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HOME/.local/share/lens-desktop/venv"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

say "Checking system packages"
missing=()
for c in flameshot xclip xdotool zenity curl python3 notify-send xdg-open; do
  command -v "$c" >/dev/null || missing+=("$c")
done
if ((${#missing[@]})); then
  echo "Missing: ${missing[*]}"
  echo "Install with: sudo apt install flameshot xclip xdotool zenity curl \\"
  echo "                  python3 libnotify-bin xdg-utils"
  exit 1
fi
# The lasso overlay draws with GTK, from the system python — PyGObject in a
# venv is more trouble than it is worth.
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
  || { echo "Missing GTK bindings. Install with: sudo apt install python3-gi gir1.2-gtk-3.0"; exit 1; }
echo "all present"

say "Installing Python dependencies into $VENV"
mkdir -p "$(dirname "$VENV")"
[[ -d "$VENV" ]] || python3 -m venv "$VENV"
# chrome-lens-py does OCR/translation; websocket-client drives the panel's
# Chrome over the DevTools protocol.
"$VENV/bin/pip" install -q --upgrade pip chrome-lens-py websocket-client
"$VENV/bin/lens_scan" --help >/dev/null 2>&1 && echo "lens_scan ok"

say "Installing the Antigravity CLI (agy)"
# Google retired Gemini CLI's personal "Login with Google" on 2026-06-18;
# Google AI Pro/Ultra accounts now go through Antigravity instead.
if [[ -x "$HOME/.local/bin/agy" ]]; then
  echo "already installed: $("$HOME/.local/bin/agy" --version)"
else
  curl -fsSL https://antigravity.google/cli/install.sh | bash
fi

say "Linking ~/.local/bin/lens"
mkdir -p "$HOME/.local/bin"
ln -sfn "$HERE/lens" "$HOME/.local/bin/lens"

say "Installing the launcher icon and desktop entry"
# Chrome stamps WM_CLASS=lens-desktop on the panel window, and this entry is
# what turns that into our own name and icon in the dock and alt-tab instead
# of Chrome's.
python3 "$HERE/make_icon.py" >/dev/null
mkdir -p "$HOME/.local/share/applications"
for entry in lens-desktop lens-text lens-translate lens-ask; do
  cp "$HERE/$entry.desktop" "$HOME/.local/share/applications/$entry.desktop"
done
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
echo "installed"

say "Binding GNOME hotkeys"
python3 - <<'EOF'
import subprocess, ast
SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY    = "custom-keybindings"
BASE   = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
LENS   = subprocess.os.path.expanduser("~/.local/bin/lens")

out = subprocess.run(["gsettings", "get", SCHEMA, KEY],
                     capture_output=True, text=True).stdout.strip()
cur = ast.literal_eval(out) if out.startswith("[") else []

binds = [
    ("lens-search", "Lens: search screen region",    "<Super><Shift>s", LENS),
    ("lens-text",   "Lens: copy text from screen",   "<Super><Shift>c", f"{LENS} text"),
    ("lens-ask",    "Lens: ask Gemini about region", "<Super><Shift>a", f"{LENS} ask"),
    ("lens-translate", "Lens: translate screen text", "<Super><Shift>t", f"{LENS} tr"),
]
for slug, name, binding, cmd in binds:
    path = BASE + slug + "/"
    if path not in cur:
        cur.append(path)
    schema = f"{SCHEMA}.custom-keybinding:{path}"
    for k, v in (("name", name), ("command", cmd), ("binding", binding)):
        subprocess.run(["gsettings", "set", schema, k, v], check=True)
subprocess.run(["gsettings", "set", SCHEMA, KEY, str(cur)], check=True)
for _, _, binding, cmd in binds:
    print(f"  {binding:20} {cmd}")
EOF

say "Done"
cat <<'MSG'
One manual step remains — log Antigravity into your Google account, in a real
terminal (the login prompt needs a TTY and times out after 60s):

    agy

Complete the browser flow. After that `lens ask` works and usage counts
against the Google AI Pro subscription.
MSG
