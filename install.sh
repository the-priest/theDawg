#!/usr/bin/env bash
#
# TheDawg installer  (Linux + macOS)
# ----------------------------------
# One-line install / update (paste in a terminal):
#
#   curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
#
# Or, from a clone:   ./install.sh
#
# What it does (no root needed — everything under $HOME):
#   - checks for python3 (>= 3.8)
#   - fetches the LATEST repo into ~/.local/share/thedawg     (git, or tarball fallback)
#   - drops a `thedawg` launcher into ~/.local/bin            (so you can just type `thedawg`)
#   - installs icons + a .desktop entry on Linux              (appears in your app menu)
#   - makes sure ~/.local/bin is on your PATH
#   - explains how to set the API key for your provider of choice
#
# Running it again = updating. It always pulls the latest from GitHub unless you
# explicitly run a local ./install.sh from a separate checkout.
#
set -euo pipefail

REPO="the-priest/theDawg"
BRANCH="main"
SRC_DIR="$HOME/.local/share/thedawg"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor"
APP_DIR="$HOME/.local/share/applications"
LAUNCHER="$BIN_DIR/thedawg"
TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"

OS="$(uname -s)"

# ---- uninstall ----
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
  echo
  echo "  removing TheDawg"
  rm -rf "$SRC_DIR"
  rm -f  "$LAUNCHER" "$BIN_DIR/clidawg" "$APP_DIR/thedawg.desktop"
  rm -f  "$ICON_DIR"/*/apps/thedawg.png "$ICON_DIR"/scalable/apps/thedawg.svg
  rm -f  "$ICON_DIR"/*/apps/io.github.the_priest.TheDawg.png \
         "$ICON_DIR"/scalable/apps/io.github.the_priest.TheDawg.svg
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
  gtk-update-icon-cache -f -t "$ICON_DIR" >/dev/null 2>&1 || true
  echo "  gone. your config and saved tools are untouched:"
  echo "    ~/.config/thedawg    (api keys, window state)"
  echo "    ~/thedawg-tools      (tools you saved)"
  echo "  delete those by hand if you want them gone too."
  echo
  exit 0
fi

# ---- pretty ----
if [ -t 1 ]; then
  B="\033[1m"; R="\033[0m"; AMBER="\033[38;5;179m"; LIME="\033[38;5;149m"
  RED="\033[38;5;167m"; GREY="\033[38;5;245m"
else
  B=""; R=""; AMBER=""; LIME=""; RED=""; GREY=""
fi
say()  { printf "${AMBER}${B}::${R} %b\n" "$1"; }
ok()   { printf "  ${LIME}\xe2\x9c\x93${R} %b\n" "$1"; }
warn() { printf "  ${RED}\xe2\x9a\xa0${R} %b\n" "$1"; }
step() { printf "  ${GREY}\xe2\x80\xa6 %b${R}\n" "$1"; }

printf "\n${AMBER}${B}  TheDawg installer${R}  ${GREY}\xe2\x80\x94 ${REPO}${R}\n"
printf "  ${GREY}AI Python GUI toolsmith \xe2\x80\x94 native app on Linux${R}\n\n"

# ---- python ----
say "checking python"
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found"
  case "$OS" in
    Linux)  printf "    install it:  ${B}sudo pacman -S python${R}     (CachyOS/Arch/Manjaro)\n"
            printf "    or:          ${B}sudo apt install python3${R}  (Debian/Ubuntu/Mint/Kali)\n"
            printf "    or:          ${B}sudo dnf install python3${R}  (Fedora)\n" ;;
    Darwin) printf "    install it:  ${B}brew install python3${R}        (Homebrew)\n"
            printf "    or grab it from https://python.org\n" ;;
  esac
  exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 1)'; then
  warn "python3 $PYV is too old — TheDawg needs 3.8 or newer"
  exit 1
fi
ok "python3 $PYV"

# ---- distro ----
# Which package manager this box uses decides two things: what we offer to install
# for the native window, and (inside TheDawg) which install commands the model is
# taught to put in the tools it writes.
DISTRO_ID=""; DISTRO_PRETTY="$OS"; FAMILY="other"; PM=""
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO_ID="${ID:-}"; DISTRO_PRETTY="${PRETTY_NAME:-${NAME:-Linux}}"
  case " ${ID:-} ${ID_LIKE:-} " in
    *cachyos*|*arch*|*manjaro*|*endeavouros*|*garuda*|*artix*) FAMILY="arch";   PM="sudo pacman -S --needed" ;;
    *debian*|*ubuntu*|*kali*|*mint*|*pop*)                     FAMILY="debian"; PM="sudo apt install" ;;
    *fedora*|*rhel*|*centos*|*nobara*)                         FAMILY="fedora"; PM="sudo dnf install" ;;
    *suse*)                                                    FAMILY="suse";   PM="sudo zypper install" ;;
  esac
fi
[ "$OS" = "Linux" ] && ok "$DISTRO_PRETTY  (${FAMILY} family)"
[ "$DISTRO_ID" = "cachyos" ] && ok "CachyOS detected \xe2\x80\x94 native window path enabled"

# packages for the native GTK4 window, per family
case "$FAMILY" in
  arch)   NATIVE_PKGS="python-gobject gtk4 libadwaita webkit2gtk-6.0" ;;
  debian) NATIVE_PKGS="python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-webkit-6.0" ;;
  fedora) NATIVE_PKGS="python3-gobject gtk4 libadwaita webkitgtk6.0" ;;
  suse)   NATIVE_PKGS="python3-gobject gtk4 libadwaita webkit2gtk3-soup2" ;;
  *)      NATIVE_PKGS="" ;;
esac

have_native() {
  python3 - <<'PYCHK' >/dev/null 2>&1
import gi
gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); gi.require_version("WebKit","6.0")
from gi.repository import Gtk, Adw, WebKit
PYCHK
}

# ---- decide the source ----
# A genuine local checkout means: this script exists as a real file on disk, sits
# next to thedawg.py, AND that folder is NOT the install dir itself.
#
# When run via `curl | bash`, the script has no on-disk path, so [ -f "$SCRIPT" ]
# is false and we ALWAYS fall through to GitHub. This is the fix for the old bug
# where piping from inside ~/.local/share/thedawg made it copy the folder onto
# itself ("are the same file") and silently skip the update.
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
LOCAL_SRC=""
if [ -f "$SCRIPT_PATH" ]; then
  CAND="$( cd "$( dirname "$SCRIPT_PATH" )" 2>/dev/null && pwd || true )"
  if [ -n "$CAND" ] && [ -f "$CAND/thedawg.py" ] && [ "$CAND" != "$SRC_DIR" ]; then
    LOCAL_SRC="$CAND"
  fi
fi

mkdir -p "$SRC_DIR" "$BIN_DIR" "$APP_DIR" \
  "$ICON_DIR/512x512/apps" "$ICON_DIR/256x256/apps" \
  "$ICON_DIR/128x128/apps" "$ICON_DIR/scalable/apps"

fetch_tarball() {
  step "downloading latest tarball"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$TARBALL" | tar xz -C "$SRC_DIR" --strip-components=1
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- "$TARBALL" | tar xz -C "$SRC_DIR" --strip-components=1
  else
    warn "need git, curl, or wget to fetch the source"
    exit 1
  fi
}

say "fetching source"
if [ -n "$LOCAL_SRC" ]; then
  step "installing from local checkout: $LOCAL_SRC"
  cp -rf "$LOCAL_SRC/thedawg.py" "$LOCAL_SRC/ui" "$LOCAL_SRC/assets" "$SRC_DIR/"
  [ -f "$LOCAL_SRC/shell.py" ] && cp -f "$LOCAL_SRC/shell.py" "$SRC_DIR/" || true
  [ -f "$LOCAL_SRC/clidawg.py" ] && cp -f "$LOCAL_SRC/clidawg.py" "$SRC_DIR/" || true
  mkdir -p "$SRC_DIR/sounds"
  [ -d "$LOCAL_SRC/sounds" ] && cp -rf "$LOCAL_SRC/sounds/." "$SRC_DIR/sounds/" || true
  [ -f "$LOCAL_SRC/README.md" ] && cp -f "$LOCAL_SRC/README.md" "$SRC_DIR/" || true
  [ -f "$LOCAL_SRC/LICENSE" ]   && cp -f "$LOCAL_SRC/LICENSE"   "$SRC_DIR/" || true
elif [ -d "$SRC_DIR/.git" ] && command -v git >/dev/null 2>&1; then
  step "updating existing checkout (git)"
  git -C "$SRC_DIR" fetch --depth 1 origin "$BRANCH" --quiet || true
  git -C "$SRC_DIR" reset --hard "origin/$BRANCH" --quiet \
    || git -C "$SRC_DIR" pull --ff-only --quiet || true
elif [ -f "$SRC_DIR/thedawg.py" ]; then
  # existing non-git install — overlay latest code, keep your sounds/config in place
  fetch_tarball
elif command -v git >/dev/null 2>&1; then
  step "git clone $REPO"
  tmp="$(mktemp -d)"
  if git clone --depth 1 -b "$BRANCH" "https://github.com/$REPO.git" "$tmp" --quiet; then
    cp -rf "$tmp/." "$SRC_DIR/"
    rm -rf "$tmp"
  else
    rm -rf "$tmp"
    fetch_tarball
  fi
else
  fetch_tarball
fi

if [ ! -f "$SRC_DIR/thedawg.py" ]; then
  warn "source fetch failed — $SRC_DIR/thedawg.py is missing"
  exit 1
fi
ok "source at $SRC_DIR"

# ---- clidawg launcher ----
if [ -f "$SRC_DIR/clidawg.py" ]; then
  cat > "$BIN_DIR/clidawg" <<EOCLI
#!/usr/bin/env bash
exec python3 "$SRC_DIR/clidawg.py" "\$@"
EOCLI
  chmod +x "$BIN_DIR/clidawg"
  ok "launcher: $BIN_DIR/clidawg  (terminal front end)"
  if ! python3 -c "import textual" >/dev/null 2>&1; then
    step "clidawg's TUI wants Textual:  pip install --user textual"
    step "  (without it, clidawg --plain still works)"
  fi
fi

# ---- native window ----
# TheDawg runs as a real GTK4 application rather than a browser in app mode. That
# needs three system libraries; without them it still works, just in a browser
# window. We never install anything without asking.
if [ "$OS" = "Linux" ]; then
  say "checking the native app window"
  if have_native; then
    ok "GTK4 + WebKitGTK present \xe2\x80\x94 TheDawg will open as a real app"
  elif [ -n "$NATIVE_PKGS" ]; then
    warn "GTK4 + WebKitGTK not installed"
    printf "    without it TheDawg falls back to a browser window\n"
    printf "    install:  ${B}${PM} ${NATIVE_PKGS}${R}\n"
    if [ -t 1 ] && [ -r /dev/tty ]; then
      printf "  ${GREY}install it now? [Y/n]${R} "
      read -r ans </dev/tty || ans="n"
      case "${ans:-Y}" in
        [Yy]*|"") ${PM} ${NATIVE_PKGS} && ok "native window ready" || warn "install failed \xe2\x80\x94 run it yourself later" ;;
        *) step "skipped \xe2\x80\x94 run the line above whenever you want it" ;;
      esac
    fi
  else
    step "unknown distro \xe2\x80\x94 install PyGObject, GTK4, libadwaita and WebKitGTK 6.0 for the native window"
  fi
fi

# ---- CLI launcher ----
say "writing launcher: $LAUNCHER"
cat > "$LAUNCHER" <<EOSH
#!/usr/bin/env bash
exec python3 "$SRC_DIR/thedawg.py" "\$@"
EOSH
chmod +x "$LAUNCHER"
ok "launcher: $LAUNCHER"

# ---- icons (Linux): install every size + the scalable SVG so any desktop
#      environment (KDE Plasma, GNOME, Phosh, XFCE…) picks a crisp icon ----
[ -f "$SRC_DIR/assets/icon-512.png" ] && cp -f "$SRC_DIR/assets/icon-512.png" "$ICON_DIR/512x512/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon-256.png" ] && cp -f "$SRC_DIR/assets/icon-256.png" "$ICON_DIR/256x256/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon-128.png" ] && cp -f "$SRC_DIR/assets/icon-128.png" "$ICON_DIR/128x128/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon.svg" ]     && cp -f "$SRC_DIR/assets/icon.svg"     "$ICON_DIR/scalable/apps/thedawg.svg"

if [ "$OS" = "Linux" ]; then
  say "registering app menu entry"
  # Terminal=false because TheDawg backgrounds its own local server and opens a
  # browser app window — no terminal needed. StartupWMClass ties the window back
  # to this entry so the Dawg icon shows in the task switcher / overview.
  # StartupWMClass must match what the window actually reports. The native GTK4
  # shell sets its app_id to io.github.the_priest.TheDawg; the browser fallback
  # sets --class=thedawg. Both entries are listed so the icon resolves either way.
  cat > "$APP_DIR/thedawg.desktop" <<EODESKTOP
[Desktop Entry]
Type=Application
Name=TheDawg
GenericName=AI Python Toolsmith
Comment=Build Python GUI tools with AI
Exec=$LAUNCHER
Icon=thedawg
Terminal=false
Categories=Development;Utility;IDE;
StartupNotify=true
StartupWMClass=io.github.the_priest.TheDawg
Keywords=AI;Python;GUI;Tools;builder;
Actions=doctor;browser;

[Desktop Action doctor]
Name=Check this machine
Exec=$LAUNCHER --doctor

[Desktop Action browser]
Name=Open in browser instead
Exec=$LAUNCHER --browser
EODESKTOP
  # the GTK shell looks up its icon by app_id, so install it under that name too
  for sz in 512 256 128; do
    [ -f "$SRC_DIR/assets/icon-$sz.png" ] && \
      cp -f "$SRC_DIR/assets/icon-$sz.png" "$ICON_DIR/${sz}x${sz}/apps/io.github.the_priest.TheDawg.png"
  done
  [ -f "$SRC_DIR/assets/icon.svg" ] && \
    cp -f "$SRC_DIR/assets/icon.svg" "$ICON_DIR/scalable/apps/io.github.the_priest.TheDawg.svg"
  chmod 644 "$APP_DIR/thedawg.desktop"
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
  gtk-update-icon-cache -f -t "$ICON_DIR" >/dev/null 2>&1 || true
  kbuildsycoca6 >/dev/null 2>&1 || kbuildsycoca5 >/dev/null 2>&1 || true
  ok "app menu: TheDawg (search your launcher / app grid)"
fi

# ---- PATH ----
# The login shell decides which file to touch AND which syntax to write. This
# matters on CachyOS, which ships fish on several editions — fish does not
# understand `export PATH=...`, so the old bashrc-only path silently left
# `thedawg` off the PATH there.
case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR already on PATH" ;;
  *)
    LOGIN_SH="$(basename "${SHELL:-bash}")"
    case "$LOGIN_SH" in
      fish)
        RC="$HOME/.config/fish/config.fish"
        mkdir -p "$(dirname "$RC")"
        if ! grep -qs 'thedawg-path' "$RC" 2>/dev/null; then
          printf '\n# thedawg-path\nfish_add_path -g %s\n' "$BIN_DIR" >> "$RC"
        fi
        HINT="exec fish"
        ;;
      zsh)
        RC="$HOME/.zshrc"
        printf '\n# thedawg-path\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$RC"
        HINT="source $RC"
        ;;
      *)
        RC="$HOME/.bashrc"
        [ "$OS" = "Darwin" ] && [ ! -f "$RC" ] && RC="$HOME/.bash_profile"
        printf '\n# thedawg-path\nexport PATH="%s:$PATH"\n' "$BIN_DIR" >> "$RC"
        HINT="source $RC"
        ;;
    esac
    warn "added $BIN_DIR to PATH in $RC  (${LOGIN_SH})"
    printf "    open a new terminal, or run:  ${B}${HINT}${R}\n"
  ;;
esac

# ---- key setup hint ----
printf "\n${AMBER}${B}  set your API key${R}  (one of these, before launching — or use Settings in-app)\n"
printf "  ${B}export GROQ_API_KEY=gsk_...${R}            ${GREY}# Groq        (recommended — fast + free tier)${R}\n"
printf "  ${B}export SILICONFLOW_API_KEY=sk-...${R}       ${GREY}# SiliconFlow${R}\n"
printf "  ${B}export GOOGLE_API_KEY=AIza...${R}           ${GREY}# Google AI Studio${R}\n"
printf "  ${B}export NOVITA_API_KEY=sk_...${R}            ${GREY}# Novita AI${R}\n"
printf "  ${GREY}(add to ~/.bashrc / ~/.zshrc to persist, or set it inside TheDawg's Settings panel)${R}\n"

# ---- done ----
printf "\n${LIME}${B}  ready.${R}  launch with:\n"
printf "  ${B}thedawg${R}\n"
printf "  ${GREY}or in the terminal:${R}  ${B}clidawg${R}\n"
printf "  ${GREY}check what else this machine could use:${R}  ${B}thedawg --doctor${R}\n"
[ "$OS" = "Linux" ] && printf "  ${GREY}or pick TheDawg from your app menu${R}\n"
printf "\n"
