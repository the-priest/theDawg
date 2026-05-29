#!/usr/bin/env bash
#
# TheDawg installer  (Linux + macOS)
# ----------------------------------
# One-line install (paste in a terminal):
#
#   curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
#
# Or, from a clone:   ./install.sh
#
# What it does (no root needed — everything under $HOME):
#   - checks for python3 (>= 3.8)
#   - fetches the repo into ~/.local/share/thedawg          (git, or tarball fallback)
#   - drops a `thedawg` launcher into ~/.local/bin          (so you can just type `thedawg`)
#   - installs an icon + a .desktop entry on Linux           (appears in your app menu)
#   - makes sure ~/.local/bin is on your PATH
#   - explains how to set the API key for your provider of choice
#
set -euo pipefail

REPO="the-priest/theDawg"
BRANCH="main"
SRC_DIR="$HOME/.local/share/thedawg"
BIN_DIR="$HOME/.local/bin"
ICON_DIR="$HOME/.local/share/icons/hicolor"
APP_DIR="$HOME/.local/share/applications"
LAUNCHER="$BIN_DIR/thedawg"

OS="$(uname -s)"

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
printf "  ${GREY}cross-platform Python toolsmith${R}\n\n"

# ---- python ----
say "checking python"
if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not found"
  case "$OS" in
    Linux)  printf "    install it:  ${B}sudo apt install python3${R}  (Debian/Ubuntu/Mint/Kali)\n"
            printf "    or:          ${B}sudo dnf install python3${R}    (Fedora)\n" ;;
    Darwin) printf "    install it:  ${B}brew install python3${R}        (Homebrew)\n"
            printf "    or grab it from https://python.org\n" ;;
  esac
  exit 1
fi
PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
ok "python3 $PYV"

# ---- fetch source ----
say "fetching source"
mkdir -p "$SRC_DIR" "$BIN_DIR" "$APP_DIR" \
  "$ICON_DIR/512x512/apps" "$ICON_DIR/256x256/apps" \
  "$ICON_DIR/128x128/apps" "$ICON_DIR/scalable/apps"
SELF_DIR="$( cd "$( dirname "${BASH_SOURCE[0]:-$0}" )" 2>/dev/null && pwd || true )"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/thedawg.py" ]; then
  step "using local copy at $SELF_DIR"
  cp -rf "$SELF_DIR/thedawg.py" "$SELF_DIR/ui" "$SELF_DIR/assets" "$SRC_DIR/"
  mkdir -p "$SRC_DIR/sounds"
  [ -d "$SELF_DIR/sounds" ] && cp -rf "$SELF_DIR/sounds/." "$SRC_DIR/sounds/"
  [ -f "$SELF_DIR/README.md" ] && cp -f "$SELF_DIR/README.md" "$SRC_DIR/" || true
  [ -f "$SELF_DIR/LICENSE" ]   && cp -f "$SELF_DIR/LICENSE"   "$SRC_DIR/" || true
else
  if command -v git >/dev/null 2>&1; then
    if [ -d "$SRC_DIR/.git" ]; then
      step "pulling latest"
      git -C "$SRC_DIR" pull --ff-only --quiet || true
    else
      step "git clone $REPO"
      rm -rf "$SRC_DIR"
      git clone --depth 1 -b "$BRANCH" "https://github.com/$REPO.git" "$SRC_DIR" --quiet
    fi
  else
    TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
    step "downloading tarball"
    if command -v curl >/dev/null 2>&1; then
      curl -fsSL "$TARBALL" | tar xz -C "$SRC_DIR" --strip-components=1
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- "$TARBALL" | tar xz -C "$SRC_DIR" --strip-components=1
    else
      warn "need git, curl, or wget"
      exit 1
    fi
  fi
fi
ok "source at $SRC_DIR"

# ---- CLI launcher ----
say "writing launcher: $LAUNCHER"
cat > "$LAUNCHER" <<EOSH
#!/usr/bin/env bash
exec python3 "$SRC_DIR/thedawg.py" "\$@"
EOSH
chmod +x "$LAUNCHER"
ok "launcher: $LAUNCHER"

# ---- icons (Linux) — install every size + the scalable SVG so Phosh (mobile GNOME
#      on the OnePlus 6) and KDE Plasma both pick a crisp icon at any DPI ----
[ -f "$SRC_DIR/assets/icon-512.png" ] && cp -f "$SRC_DIR/assets/icon-512.png" "$ICON_DIR/512x512/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon-256.png" ] && cp -f "$SRC_DIR/assets/icon-256.png" "$ICON_DIR/256x256/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon-128.png" ] && cp -f "$SRC_DIR/assets/icon-128.png" "$ICON_DIR/128x128/apps/thedawg.png"
[ -f "$SRC_DIR/assets/icon.svg" ]     && cp -f "$SRC_DIR/assets/icon.svg"     "$ICON_DIR/scalable/apps/thedawg.svg"

if [ "$OS" = "Linux" ]; then
  say "registering app menu entry"
  # Terminal=false: Phosh's app grid has no terminal wired by default, so a
  # Terminal=true entry silently fails to launch there. TheDawg backgrounds its
  # own local server and opens a browser window, so it needs no terminal.
  # StartupWMClass ties the running window back to this entry so the Dawg icon
  # shows in the KDE task switcher / Phosh overview instead of a generic one.
  cat > "$APP_DIR/thedawg.desktop" <<EODESKTOP
[Desktop Entry]
Type=Application
Name=TheDawg
GenericName=AI Python Toolsmith
Comment=Build Linux Python GUI tools with AI
Exec=$LAUNCHER
Icon=thedawg
Terminal=false
Categories=Development;Utility;
StartupNotify=true
StartupWMClass=thedawg
Keywords=AI;Python;GUI;Tools;
EODESKTOP
  chmod 644 "$APP_DIR/thedawg.desktop"
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
  gtk-update-icon-cache -f -t "$ICON_DIR" >/dev/null 2>&1 || true   # GTK / Phosh
  kbuildsycoca6 >/dev/null 2>&1 || kbuildsycoca5 >/dev/null 2>&1 || true  # KDE Plasma menu cache
  ok "app menu: TheDawg (search your launcher / app grid)"
fi

# ---- PATH ----
case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR already on PATH" ;;
  *)
    RC="$HOME/.bashrc"
    [ -n "${ZSH_VERSION:-}" ] && RC="$HOME/.zshrc"
    [ "$OS" = "Darwin" ] && [ ! -f "$RC" ] && RC="$HOME/.zshrc"
    printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$RC"
    warn "added $BIN_DIR to PATH in $RC"
    printf "    open a new terminal, or run:  ${B}source $RC${R}\n"
  ;;
esac

# ---- key setup hint ----
printf "\n${AMBER}${B}  set your API key${R}  (one of these, before launching)\n"
printf "  ${B}export GROQ_API_KEY=gsk_...${R}            ${GREY}# Groq        (recommended — fast + free tier)${R}\n"
printf "  ${B}export SILICONFLOW_API_KEY=sk-...${R}       ${GREY}# SiliconFlow${R}\n"
printf "  ${B}export GOOGLE_API_KEY=AIza...${R}           ${GREY}# Google AI Studio${R}\n"
printf "  ${B}export NOVITA_API_KEY=sk_...${R}            ${GREY}# Novita${R}\n"
printf "  ${GREY}(add to ~/.bashrc / ~/.zshrc to persist, or set it inside TheDawg's Settings panel)${R}\n"

# ---- done ----
printf "\n${LIME}${B}  ready.${R}  launch with:\n"
printf "  ${B}thedawg${R}\n"
[ "$OS" = "Linux" ] && printf "  ${GREY}or pick TheDawg from your app menu${R}\n"
printf "\n"
