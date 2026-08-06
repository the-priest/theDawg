#!/usr/bin/env python3
"""
TheDawg  -  AI-assisted Linux Python toolsmith
==============================================
A local workspace for building real, graphical Python tools by talking to a model —
native GUI tools for the Linux desktop, tuned for KDE Plasma on X11 and at home on any
desktop (GNOME, XFCE, Cinnamon, …) under either Wayland or X11. You agree on the tool in
a build dialogue, TheDawg writes a TESTING version you launch right here on YOUR box, you
iterate on real behaviour, and only when you ask does it package a RELEASE version. Then
with one button it can also pack the tool into a single-file Linux binary via PyInstaller.

Built for the Linux desktop from the ground up:
  - paths via pathlib + XDG dirs (~/.config, ~/.local/share)
  - process management with POSIX session/signal handling
  - GUI toolkits installed via pip into a managed venv
  - generated tools ship an install.sh (curl|bash) and a .desktop entry with an icon
  - the model is taught to write desktop GUI code that works under BOTH Wayland and X11,
    looks intentional, and never freezes the window

This file is a tiny local HTTP server (standard library only). It:
  - serves the workspace UI to your browser
  - keeps your API key on THIS machine (never sent to the browser)
  - LAUNCHES the generated GUI locally so "test it" is real
  - is GUI-aware: detects the toolkit, runs with the right interpreter, surfaces
    startup errors, doesn't block waiting for a window you left open
  - never auto-runs anything: you click run, and a destructive-pattern scan guards it

Run:
    export SILICONFLOW_API_KEY="sk-..."   # primary provider
    export GROQ_API_KEY="gsk_..."         # fallback provider
    python3 thedawg.py                    # opens http://127.0.0.1:8765 in your browser

License: MIT
"""

import os
import re
import sys
import json
import time
import shlex
import shutil
import signal
import socket
import platform
import tempfile
import threading
import webbrowser
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "2.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# PLATFORM DETECTION  -- every cross-platform branch in this file reads these
# --------------------------------------------------------------------------
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC

def detect_desktop_env():
    """Classify the running Linux session for the UI. Returns a dict:

      {"de": "kde"|"gnome"|"xfce"|"cinnamon"|"other", "form": "desktop",
       "session": "wayland"|"x11"|"unknown", "raw": "<XDG_CURRENT_DESKTOP>"}

    TheDawg targets the Linux desktop, so `form` is always "desktop"; `de` and
    `session` are informational. Detection reads the freedesktop env vars every
    session sets (XDG_CURRENT_DESKTOP, XDG_SESSION_TYPE/WAYLAND_DISPLAY) and
    degrades gracefully to "other"/"desktop" when nothing is set.
    """
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "") or ""
    desk = raw.lower()
    sess = (os.environ.get("XDG_SESSION_TYPE", "") or "").lower()
    if not sess:
        sess = "wayland" if os.environ.get("WAYLAND_DISPLAY") else (
            "x11" if os.environ.get("DISPLAY") else "unknown")

    if "kde" in desk or "plasma" in desk:
        de = "kde"
    elif "gnome" in desk:
        de = "gnome"
    elif "xfce" in desk:
        de = "xfce"
    elif "cinnamon" in desk:
        de = "cinnamon"
    else:
        de = "other"

    return {"de": de, "form": "desktop", "session": sess, "raw": raw}

# --------------------------------------------------------------------------
# DISTRO DETECTION  -- so every package hint TheDawg prints (and every hint it
# TEACHES the model to print) is correct for the machine it is actually on.
# CachyOS is the reference target: Arch-based, pacman, KDE Plasma 6 on Wayland,
# x86-64-v3/v4 optimised packages. Debian/Fedora/SUSE stay supported.
# --------------------------------------------------------------------------
_OSREL = None

def _os_release():
    """Parse /etc/os-release once. Returns a dict (empty on non-Linux)."""
    global _OSREL
    if _OSREL is not None:
        return _OSREL
    data = {}
    for p in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    data[k.strip()] = v.strip().strip('"').strip("'")
            break
        except Exception:
            continue
    _OSREL = data
    return data


# logical name -> per-family package name. None = "not a separate package here".
PKG_TABLE = {
    #                 arch                     debian              fedora                    suse
    "tk":            ("tk",                    "python3-tk",       "python3-tkinter",        "python3-tk"),
    "xvfb":          ("xorg-server-xvfb",      "xvfb",             "xorg-x11-server-Xvfb",   "xorg-x11-server-Xvfb"),
    "xdotool":       ("xdotool",               "xdotool",          "xdotool",                "xdotool"),
    "imagemagick":   ("imagemagick",           "imagemagick",      "ImageMagick",            "ImageMagick"),
    "pillow":        ("python-pillow",         "python3-pil",      "python3-pillow",         "python3-Pillow"),
    "pygobject":     ("python-gobject",        "python3-gi",       "python3-gobject",        "python3-gobject"),
    "gtk4":          ("gtk4",                  "libgtk-4-1",       "gtk4",                   "gtk4"),
    "libadwaita":    ("libadwaita",            "gir1.2-adw-1",     "libadwaita",             "libadwaita"),
    "webkitgtk6":    ("webkit2gtk-6.0",        "gir1.2-webkit-6.0","webkitgtk6.0",           "webkit2gtk3-soup2"),
    "pyqt6":         ("python-pyqt6",          "python3-pyqt6",    "python3-qt6",            "python3-qt6"),
    "pyside6":       ("pyside6",               "python3-pyside6",  "python3-pyside6",        "python3-pyside6"),
    "pyinstaller":   ("pyinstaller",           "pyinstaller",      "pyinstaller",            "pyinstaller"),
    "ruff":          ("ruff",                  "ruff",             "ruff",                   "ruff"),
    "jetbrains-mono":("ttf-jetbrains-mono",    "fonts-jetbrains-mono", "jetbrains-mono-fonts","jetbrains-mono-fonts"),
    "inter":         ("inter-font",            "fonts-inter",      "rsms-inter-fonts",       "inter-font"),
}
_FAM_IDX = {"arch": 0, "debian": 1, "fedora": 2, "suse": 3}

# how each family installs things
_INSTALL_CMD = {
    "arch":   "sudo pacman -S --needed",
    "debian": "sudo apt install",
    "fedora": "sudo dnf install",
    "suse":   "sudo zypper install",
}


def detect_distro():
    """Classify the running Linux distribution.

    Returns {"id","like","family","name","pretty","cachy","install","update"} where
    family is one of arch|debian|fedora|suse|other and `install` is the literal
    command prefix used to install a package on this box.
    """
    if not IS_LINUX:
        return {"id": "", "like": "", "family": "other", "name": platform.system(),
                "pretty": platform.system(), "cachy": False,
                "install": "", "update": ""}
    d = _os_release()
    did = (d.get("ID") or "").lower()
    like = (d.get("ID_LIKE") or "").lower()
    blob = did + " " + like
    if any(k in blob for k in ("cachyos", "arch", "manjaro", "endeavouros", "garuda", "artix")):
        fam = "arch"
    elif any(k in blob for k in ("debian", "ubuntu", "kali", "linuxmint", "mint", "raspbian", "pop")):
        fam = "debian"
    elif any(k in blob for k in ("fedora", "rhel", "centos", "nobara", "bazzite")):
        fam = "fedora"
    elif any(k in blob for k in ("suse", "opensuse")):
        fam = "suse"
    else:
        fam = "other"
    return {
        "id": did,
        "like": like,
        "family": fam,
        "name": d.get("NAME", "Linux"),
        "pretty": d.get("PRETTY_NAME", d.get("NAME", "Linux")),
        "cachy": did == "cachyos" or "cachyos" in blob,
        "install": _INSTALL_CMD.get(fam, ""),
        "update": {"arch": "sudo pacman -Syu", "debian": "sudo apt update && sudo apt upgrade",
                   "fedora": "sudo dnf upgrade", "suse": "sudo zypper up"}.get(fam, ""),
    }


DISTRO = detect_distro()


def pkg(*logical):
    """Package names for this distro family. Unknown/absent entries are dropped."""
    idx = _FAM_IDX.get(DISTRO["family"])
    out = []
    for name in logical:
        row = PKG_TABLE.get(name)
        if not row:
            continue
        val = row[idx] if idx is not None else row[0]
        if val:
            out.append(val)
    return out


def install_line(*logical):
    """A copy-pasteable install command for this distro, e.g.
    `sudo pacman -S --needed xorg-server-xvfb xdotool imagemagick`.
    Falls back to a neutral phrasing on unknown distros."""
    names = pkg(*logical)
    if not names:
        return ""
    if not DISTRO["install"]:
        return "install with your package manager: " + " ".join(names)
    return f"{DISTRO['install']} {' '.join(names)}"


def is_cachy():
    return bool(DISTRO.get("cachy"))


def cpu_threads():
    """Usable parallelism, honouring cgroup/affinity limits rather than raw core count."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        return max(1, os.cpu_count() or 1)


def app_data_dir():
    """Per-OS app data dir (writes that should persist + survive)."""
    if IS_WIN:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "TheDawg"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "TheDawg"
    return Path.home() / ".local" / "share" / "thedawg"

def config_dir():
    """Per-OS config dir (small settings file)."""
    if IS_WIN:
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "TheDawg"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "TheDawg"
    return Path.home() / ".config" / "thedawg"

def tools_dir():
    """Where built/saved tools live, under the user's home (visible, not hidden)."""
    return Path.home() / "TheDawg-tools" if IS_WIN else Path.home() / "thedawg-tools"

# ==========================================================================
# CONFIG  -- yours to edit
# ==========================================================================

# --------------------------------------------------------------------------
# PROVIDERS
# --------------------------------------------------------------------------
# TheDawg can call several providers. You pick one per session in the UI; if a
# call fails it falls through that provider's own model chain (biggest first).
# Keys are read from env vars (below) or pasted in Settings. Nothing is sent to
# the browser; keys persist to an owner-only config file.
#
# The "models" lists below are only FALLBACKS. TheDawg fetches each provider's
# live catalog from its OpenAI-compatible /models endpoint ("models_url") using
# your key, so the dropdown shows exactly what your account can actually call —
# no more guessing at names that 404 with "model unavailable on your plan".
PROVIDERS = {
    "groq": {
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "models_url": "https://api.groq.com/openai/v1/models",
        "env": "GROQ_API_KEY",
        "kind": "openai",
        "models": [
            "llama-3.3-70b-versatile",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "gemma2-9b-it",
            "llama-3.1-8b-instant",
        ],
    },
    "siliconflow": {
        "label": "SiliconFlow",
        # SiliconFlow runs TWO separate platforms whose keys are NOT interchangeable:
        #   - International: cloud.siliconflow.COM  -> api.siliconflow.com
        #   - China:         cloud.siliconflow.CN   -> api.siliconflow.cn
        # A key made on one returns 401 on the other. We target .com because that's
        # where cloud.siliconflow.com keys are issued. If your key is from the .cn
        # site instead, change both URLs below back to .cn.
        "url": "https://api.siliconflow.com/v1/chat/completions",
        "models_url": "https://api.siliconflow.com/v1/models?sub_type=chat",
        "env": "SILICONFLOW_API_KEY",
        "kind": "openai",
        # V4 Flash first — your chosen primary: 1M context, fast, far cheaper than Pro.
        # The rest are fallbacks only; a live /models fetch overrides this list.
        "models": [
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-ai/DeepSeek-V3",
            "Qwen/Qwen2.5-72B-Instruct",
            "Qwen/Qwen2.5-Coder-32B-Instruct",
            "Qwen/Qwen2.5-7B-Instruct",
        ],
    },
    "google": {
        "label": "Google AI Studio",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "models_url": "https://generativelanguage.googleapis.com/v1beta/openai/models",
        "env": "GOOGLE_API_KEY",
        "kind": "openai",   # google exposes an OpenAI-compatible endpoint
        "models": [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
    },
    "novita": {
        "label": "Novita AI",
        "url": "https://api.novita.ai/v3/openai/chat/completions",
        "models_url": "https://api.novita.ai/v3/openai/models",
        "env": "NOVITA_API_KEY",
        "kind": "openai",
        "models": [
            "deepseek/deepseek-v3",
            "qwen/qwen-2.5-72b-instruct",
            "meta-llama/llama-3.1-70b-instruct",
            "openai/gpt-oss-120b",
            "meta-llama/llama-3.1-8b-instruct",
        ],
    },
}

# default provider on first launch: SiliconFlow primary, Groq is the fallback.
DEFAULT_PROVIDER = "siliconflow"
# when no model is explicitly chosen, prefer this one on the default provider.
# DeepSeek V4 Flash is the primary: 1M context, fast, and far cheaper than V4 Pro.
DEFAULT_MODEL_BY_PROVIDER = {
    "siliconflow": "deepseek-ai/DeepSeek-V4-Flash",
}
# providers tried in order if the primary provider's whole chain fails outright.
FALLBACK_PROVIDERS = ["groq"]

# auto-test loop: after the model writes code, TheDawg silently checks it and
# feeds failures back to the model up to this many times before showing you.
AUTOTEST_MAX_ROUNDS = 3

# temperature used ONLY for code generation / auto-fix. Lower than the 0.3 default
# used elsewhere: code wants determinism, not creativity — fewer invented APIs and
# careless mistakes, more reproducible output.
BUILD_TEMPERATURE = 0.15

HOST = "127.0.0.1"
PORT = 8765

# This is the heart of it: the model is taught to build GRAPHICAL tools the way a
# careful senior engineer does -- agree first, testing version by default, release
# only on request. Package-manager lines are substituted for the ACTUAL distro this
# copy of TheDawg is running on (see DISTRO / install_line), so a tool built on
# CachyOS tells you `pacman`, not `apt`.
SYSTEM_PROMPT_TMPL = """You are TheDawg, a senior Python engineer who builds small, sharp, genuinely
working GRAPHICAL (GUI) tools for the LINUX DESKTOP as a single-file script. The machine you are
building for RIGHT NOW is: __DISTRO_PRETTY__ (__DISTRO_FAMILY__ family, package manager
`__PKG_MGR__`), desktop __DESKTOP__ on __SESSION__. Target that box first and stay portable to other
Linux desktops. Every tool you produce opens a real window — never a bare command-line script.

Write the code a careful professional ships: correct, defensive, readable, responsive — and it must
LOOK intentional and feel good to use, not like a thrown-together debug window. Hold that bar no
matter how casually the request is phrased.

TOOLKIT — pick exactly ONE per tool and honour the user's choice from the intake:
- PyQt6 / PySide6 — most polished and feature-rich, and the natural fit for KDE Plasma. Default
  choice for anything with tables, tabs, docks, or real data. System package: `__PKG_QT__`;
  otherwise `pip install PyQt6` (TheDawg installs into a managed venv).
- GTK4 + libadwaita (PyGObject) — the most native-looking option on GNOME and a clean modern look
  everywhere. Use `gi.require_version("Gtk", "4.0")` before importing Gtk. System package:
  `__PKG_GTK__`. There is no pip wheel — it must come from the distro, so say so plainly if missing.
- CustomTkinter — modern themed Tkinter, one `pip install customtkinter`. Good middle ground when
  the user wants something prettier than raw Tk without a heavy dependency.
- Tkinter — standard library, zero pip. Needs the system Tk package: `__PKG_TK__`. The safest pick
  for a small utility that must just work.
- wxPython — only if explicitly requested.
Never mix toolkits in one tool. Never import a toolkit you did not pick.

THE SHAPE OF EVERY TOOL — follow this structure, adapted to the chosen toolkit:

    #!/usr/bin/env python3
    \"\"\"<ToolName> — one-line summary. Launch: python3 <toolname>.py\"\"\"
    import ...                      # stdlib first
    try:
        <toolkit import>
    except ImportError:
        raise SystemExit("<ToolName> needs <Toolkit>.\\n  __PKG_QT__\\n  or: pip install PyQt6")

    APP_NAME = "<toolname>"
    CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME

    class App(<Window base>):
        def __init__(self):
            ...                     # build widgets, store EVERY one the callbacks need on self
        def on_<action>(self):
            ...                     # validate -> disable button -> start worker thread
        def _worker(self, ...):
            ...                     # no widget touched here; results marshalled back
        def _done(self, result):
            ...                     # runs on the GUI thread; re-enable in a finally

    def main():
        ...                         # construct + run
    if __name__ == "__main__":
        main()

Top level holds imports, constants and definitions ONLY. Nothing that opens a window, touches a
display, or blocks at import time — TheDawg imports your module to pre-check it before you ever see
a window.

LINUX ENGINEERING — non-negotiable on every tool:
- Paths: `pathlib.Path` always. `~/.config/<app>` for settings, `~/.local/share/<app>` for data,
  `tempfile.gettempdir()` for temp. Honour `$XDG_CONFIG_HOME` / `$XDG_DATA_HOME`. Never hardcode
  "/tmp/..." or "/home/user".
- Subprocesses: list argv, never `shell=True` with user input. Locate binaries with `shutil.which`
  and, when one is missing, show an in-window message naming the package and the exact command for
  THIS distro: `__PKG_MGR__ <package>`. Never a silent failure, never a raw traceback dialog.
- Encoding: `encoding="utf-8"` on every `open()` and every text-mode `subprocess.run/Popen`, with
  `errors="replace"` when reading tool output.
- POSIX directly is fine (os.setsid, signal.SIGTERM). Never import msvcrt / winreg / win32api and
  never write a Windows branch.
- DISPLAY SERVER: the same code runs under BOTH Wayland and X11 — __SESSION__ is what is running
  here. Never hardcode `DISPLAY=:0`; never depend on xdotool/wmctrl for core function; use the
  toolkit's own clipboard/screenshot/window APIs. If a feature is genuinely X11-only, detect
  `os.environ.get("WAYLAND_DISPLAY")` and degrade with an explanation in the window.
- Root: if an action needs privileges, do NOT run the whole app as root — shell out through
  `pkexec` (or tell the user to run that one command), and say why in the UI.

UX & VISUAL QUALITY — this is what separates a tool you keep from one you delete:
- WINDOW: descriptive title (the tool's name, never "tk"), sensible default size (~820x600),
  a minimum size so the layout can't collapse, resizable, WM decides placement.
- SPACING: consistent padding — Tk `padx/pady` 8–12, Qt layout margins 12 / spacing 8. Group
  related fields in a labelled frame/group box. Cramped edge-to-edge widgets read as unfinished.
- HIERARCHY: exactly one obvious primary action, visually distinct from secondary ones. Every
  field labelled. Destructive actions confirmed.
- KEYBOARD: focus the first meaningful field on open; Enter triggers the primary action; Escape
  closes dialogs; Ctrl+Q quits. Tab order should follow the visual order.
- FEEDBACK: any non-instant work gets a visible busy state — status label or progress bar — the
  action button disabled while it runs and re-enabled in a `finally`. Success and failure both
  shown IN THE WINDOW, never only on stderr.
- OUTPUT: the right widget for the data — a real table/treeview for rows, a scrolling monospace
  pane for logs. Selectable, and copyable or savable where that helps. Empty states get a hint
  ("drop a file here", "no results yet"), never a blank pane.
- RESTRAINT: match polish to the job. Two fields should look clean and minimal. Do not invent
  features nobody asked for.

RUNTIME CORRECTNESS — the bugs that pass a parse/import check and only bite when the window opens.
TheDawg TESTS your tool for real: it imports it, then opens the window on a headless display,
screenshots it, checks it isn't blank, and sends synthetic keys and a click to surface
crash-on-interaction. Whatever it sees comes straight back to you. Get these right first time:
- WIDGET LIFETIME: every widget a callback or thread touches later lives on `self`. In Tkinter an
  image (`PhotoImage`, `ImageTk.PhotoImage`) MUST be kept on `self` or it is garbage collected and
  the widget renders blank.
- THREAD -> GUI: a worker thread NEVER touches a widget. Tkinter: `widget.after(0, lambda: ...)`.
  Qt: emit a signal connected to a main-thread slot. GTK: `GLib.idle_add(...)`.
- CALLBACK SIGNATURES: Tk `command=` passes nothing, Tk `bind` passes an event, Qt `clicked` passes
  a bool, GTK `connect("clicked", ...)` passes the widget. Match it or wrap in a lambda. Late
  binding in loops: `lambda x=x: f(x)`, never `lambda: f(x)`.
- LIFECYCLE: exactly one root / QApplication / Adw.Application; the main loop runs exactly once, at
  the end, under `__main__`. Secondary windows are `Toplevel` / `QDialog` / `Gtk.Window`.
- LAYOUT: expandable widgets need `fill`/`expand` or `sticky` plus row/column weights, or the
  window opens empty and won't scroll. Long startup work goes in a thread with a "loading…" state.
- EXTERNAL PROCESSES: capture stdout AND stderr, check the return code, surface failures in the
  window. A non-zero exit with output only on stderr is the classic "it silently did nothing".
- STATE AFTER ERRORS: re-enable buttons and hide spinners in `finally` so one failure doesn't leave
  the UI stuck.

BANNED — any of these makes the output wrong, no exceptions:
- Truncating the script. No "# ... rest unchanged", no "# (previous code here)", no `...` standing
  in for real code. Every iteration returns the WHOLE file.
- `except: pass` / `except Exception: pass` swallowing an error the user needed to see.
- Stub callbacks, `pass  # TODO`, or a function that returns fake data while claiming to work.
- Invented APIs. If you are not certain a method exists on that class in that version, use an
  approach you are certain of.
- A second code block. Exactly one ```python block per reply, or none at all.

FINAL SELF-CHECK — run this list over your own code before you output. It is faster than a fix round:
1. Every name used is defined; every `self.x` read in a callback is assigned in `__init__`.
2. Every function and method is called with the right number and kind of arguments.
3. No widget is touched from a worker thread.
4. Every `try` either handles the failure visibly or re-raises.
5. Imports match what is actually used — nothing missing, nothing unused.
6. The script is complete from first line to last, and `if __name__ == "__main__":` is the only
   thing that starts the UI.
7. Trace it once: startup -> the main interaction -> one obvious failure path. Does each end with
   the user seeing something sensible in the window?

METHOD — the build dialogue:
1. CLARIFY FIRST. If meaningful decisions are unresolved, do not dump code — surface them. Prefer
   concrete either/or choices ("table or live log?", "save to file or copy to clipboard?") over
   open questions: TheDawg turns your questions into tappable buttons. Once the shape is clear,
   build. If TheDawg ran an intake, honour every answer exactly, including the toolkit.
2. TESTING VERSION BY DEFAULT: one complete runnable single-file GUI script. Lean but correct —
   real widgets, real behaviour, validation, threaded work, graceful errors. No packaging ceremony.
3. ITERATE on real feedback: given a run result, error or log, return the FULL updated script and
   say briefly what changed and why.
4. RELEASE VERSION ONLY WHEN ASKED: top docstring with summary and how to launch, clean classes, an
   optional minimal argparse for flags like --version that does NOT replace the GUI, robust error
   handling, useful comments, zero dead code. Still a GUI app.
5. SAFETY: no destructive operations (mass deletion, disk wipes, fork bombs) unless the user asks
   explicitly and unambiguously — and then call it out. It runs on the user's own machine.

OUTPUT FORMAT: a tight message first (a few sentences — what you built or what you changed). THEN,
only when actually providing code, exactly ONE ```python fenced block containing the entire
single-file script. When planning or asking, include no code block at all."""


def _build_system_prompt():
    """Bake the running machine's real package manager + session into the prompt once."""
    de = detect_desktop_env()
    return (SYSTEM_PROMPT_TMPL
            .replace("__DISTRO_PRETTY__", DISTRO.get("pretty") or "Linux")
            .replace("__DISTRO_FAMILY__", DISTRO.get("family") or "other")
            .replace("__PKG_MGR__", DISTRO.get("install") or "your package manager")
            .replace("__DESKTOP__", (de.get("raw") or de.get("de") or "unknown"))
            .replace("__SESSION__", de.get("session") or "unknown")
            .replace("__PKG_QT__", install_line("pyqt6") or "pip install PyQt6")
            .replace("__PKG_GTK__", install_line("pygobject", "gtk4", "libadwaita")
                     or "install PyGObject + GTK4 from your distro")
            .replace("__PKG_TK__", install_line("tk") or "install Tk from your distro"))


SYSTEM_PROMPT = _build_system_prompt()

# Used to generate a tailored, clickable intake for a new tool request.
INTAKE_PROMPT_TMPL = """You are the requirements analyst for TheDawg, a builder of GRAPHICAL (GUI)
Python tools for the LINUX DESKTOP. The target machine is __DISTRO_PRETTY__ running __DESKTOP__ on
__SESSION__. The user wants to build a tool. Produce the SHORT, HIGH-VALUE set of questions needed
to build EXACTLY the right desktop GUI — no lazy or generic filler.

Return ONLY a JSON object, no prose, no markdown fences:
{"summary": "<one line restating the Linux GUI tool they want to build>",
 "questions": [
   {"q": "<clear question>", "options": ["<opt1>", "<opt2>", "<opt3>"], "multi": false},
   ...
 ]}

Rules:
- 3 to 6 questions MAX. Only ask what genuinely changes the code.
- ALWAYS include a toolkit question. Use options drawn from: ["PyQt6 / PySide6 (most polished,
  best on KDE)", "GTK4 + libadwaita (most native on GNOME)", "CustomTkinter (modern, one pip
  install)", "Tkinter (stdlib, smallest)"] — pick the 2-4 that genuinely fit THIS tool. Qt is the
  default for anything with tables or lots of state; Tkinter for a small utility.
- Tailor the rest to THIS tool: what the main window shows (table of results, live log, form +
  output pane), what inputs the user gives (fields, file picker, target/range), whether it wraps an
  external Linux binary (and which one) or is pure Python, and how results are presented or
  exported (in-window list, save to file, copy to clipboard).
- Do NOT ask which OS or which distro — it is always this Linux desktop. Only ask about an
  OS-feature when it changes the code (e.g. "show desktop notifications? — yes / no").
- 2 to 4 options per question. Concrete and mutually distinct. Set "multi": true only when picking
  several genuinely makes sense.
- Prefer options the user can just tap. Keep them short."""

INTAKE_PROMPT = (INTAKE_PROMPT_TMPL
                 .replace("__DISTRO_PRETTY__", DISTRO.get("pretty") or "Linux")
                 .replace("__DESKTOP__", detect_desktop_env().get("raw") or "a Linux desktop")
                 .replace("__SESSION__", detect_desktop_env().get("session") or "unknown"))

# Turns the model's OWN clarifying questions (asked mid-build, when it returned no
# code) into the same tappable multiple-choice block used for the opening intake — so
# EVERY time TheDawg asks you something, you can tap an answer instead of typing it.
FOLLOWUP_PROMPT = """You convert a build assistant's questions into tappable multiple-choice options.
You are given the assistant's latest message to the user (the assistant builds GUI Python tools for
Linux). If that message asks the user anything — a clarifying question, a choice between approaches,
a yes/no, which option they prefer — turn EACH such question into a clickable question with concrete
options the user can just tap.

Return ONLY a JSON object, no prose, no markdown fences:
{"questions": [
   {"q": "<the question, short>", "options": ["<concrete answer>", "..."], "multi": false},
   ...
 ]}

Rules:
- If the assistant is NOT actually asking the user to decide anything (it is only explaining,
  confirming, or reporting what it just did), return {"questions": []}. Never invent questions.
- One entry per real question the assistant asked; keep the user's wording and intent.
- 2 to 4 options each, concrete and mutually distinct, short enough to sit on a button. Offer the
  obvious real answers (include a sensible default; for a yes/no include both). Add an option like
  "no preference" / "you decide" when that is a reasonable answer.
- Set "multi": true ONLY when choosing several genuinely makes sense (e.g. "which of these
  features?"). Otherwise false.
- Max 6 questions. The user can always type a free-form reply instead, so do NOT pad — structure
  only what the assistant actually asked."""

# Used by the GitHub-ready flow to assemble repo files from the user's answers.
GITHUB_PROMPT = """You are preparing a polished GitHub release of a LINUX desktop Python GUI tool
(built and tested on __DISTRO_PRETTY__; runs on any Linux desktop under Wayland or X11). You will be given the
final code and the user's repo details. Produce a complete, professional repo.

Return ONLY a JSON object, no prose, no markdown fences:
{"readme": "<full README.md markdown>",
 "gitignore": "<.gitignore contents>",
 "requirements": "<requirements.txt for pip deps, or empty string if pure stdlib>",
 "description": "<one-line repo description>"}

README requirements:
- Title, one-line description, then a short paragraph: what the GUI does and that it is a native
  Linux desktop tool (built on __DISTRO_PRETTY__; works under Wayland or X11).
- A "Requirements" section listing Python ≥ 3.8 and the pip packages from requirements.txt (or
  noting "pure standard library" if there are none). If the tool uses Tkinter, note the system
  package line `__PKG_TK__` and give the Debian and Fedora equivalents on the next line so the
  README is useful to everyone, not just this machine.
- An "Install" section with ONE one-line installer:
    curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/<branch>/install.sh | bash
  The same line should work for updates (re-running it). Use the exact user/repo/branch given.
- A "Usage" section: launch from the app menu / launcher, or by running `<name>` from a terminal,
  and a sentence on the main window. Keep it real and copy-pasteable.
- The license name. Clean, scannable, professional. No fluff.

For "requirements": detect imports beyond the stdlib in the code. Common entries: customtkinter,
PyQt5, PyQt6, PySide6, requests, etc. Tkinter is stdlib — do NOT list it. If pure stdlib, return an
empty string."""

GITHUB_PROMPT = (GITHUB_PROMPT
                 .replace("__DISTRO_PRETTY__", DISTRO.get("pretty") or "Linux")
                 .replace("__PKG_TK__", install_line("tk") or "install Tk from your distro"))

# Used by the "review my code" button: a focused critique that DIAGNOSES, never rewrites.
REVIEW_PROMPT = """You are a senior Python/GUI engineer doing a careful code review of a single-file
Linux desktop tool (Tkinter / CustomTkinter / PyQt / PySide / GTK4), built for __DISTRO_PRETTY__
and running under either Wayland or X11. You are given the FULL code and, separately, the findings of an
automated static analyzer. Your job is to REVIEW, not rewrite — do NOT output a corrected script.

Look hard for things that will actually bite the user:
- logic errors and clashes: functions called with wrong/!args, methods that don't exist on the
  object, signals/callbacks wired to handlers that aren't defined, variables used before assignment
- GUI-specific problems: blocking work on the main thread (freezes the window), missing
  widget.after / signals when updating the UI from a thread, missing graceful handling when the
  toolkit isn't installed
- LINUX/DISPLAY: hardcoded paths like "/tmp"; missing encoding="utf-8" on text I/O; shell=True with
  user input; X11-only assumptions (xdotool/wmctrl, hardcoded DISPLAY) that break under Wayland;
  package hints for the wrong distro (this box uses `__PKG_MGR__`, not apt unless that IS apt)
- INCOMPLETENESS: truncated code, "# rest unchanged" markers, stub callbacks, `pass  # TODO`,
  or a function that returns placeholder data while presenting itself as working
- UX & POLISH: no window title or sane min-size; cramped, unpadded layout; no progress/feedback for
  slow work; the action button not disabled while running; errors shown only on stderr instead of in
  the window; a blank empty state with no hint; output crammed into a single label instead of a real
  table or scrollable text area
- correctness: unhandled error paths, resource leaks, race conditions, off-by-one, wrong defaults
- dead or contradictory code, and anything that simply won't do what it claims

Return ONLY a JSON object, no prose, no fences:
{"verdict": "<one short sentence: is it solid, or does it need work?>",
 "issues": [
   {"severity": "high|medium|low", "title": "<short>", "detail": "<what's wrong and why it matters>",
    "line": <line number or null>}
 ],
 "strengths": ["<one or two things done well>"]}

Be specific and honest. If it's genuinely clean, say so with an empty issues list — do not invent
problems. Order issues high severity first. Cap at the ~8 most important."""

REVIEW_PROMPT = (REVIEW_PROMPT
                 .replace("__DISTRO_PRETTY__", DISTRO.get("pretty") or "Linux")
                 .replace("__PKG_MGR__", DISTRO.get("install") or "your package manager"))

DANGER = [
    # POSIX
    r"rm\s+-rf\s+/", r"rm\s+-rf\s+~", r"rm\s+-rf\s+\$HOME", r"rm\s+-rf\s+\*",
    r":\(\)\s*\{", r"shutil\.rmtree\(\s*['\"]/", r"\bmkfs\b",
    r"dd\s+if=", r"\bof=/dev/sd", r"os\.system\(\s*['\"]\s*rm\b", r">\s*/dev/sd",
    r"os\.fork\s*\(\)", r"shutil\.rmtree\(\s*os\.path\.expanduser",
    # Windows
    r"format\s+[a-zA-Z]:\s*/[a-zA-Z]",                  # format c: /q
    r"del\s+/[sSqQfF]\s+/[sSqQfF]",                     # del /s /q /f ...
    r"rd\s+/[sSqQ]\s+/[sSqQ]\s+[a-zA-Z]:\\\\",          # rd /s /q C:\
    r"rmdir\s+/[sSqQ]\s+/[sSqQ]\s+[a-zA-Z]:\\\\",       # rmdir /s /q C:\
    r"cipher\s+/w:[a-zA-Z]:",                           # cipher /w:C:  (overwrite free space)
    r"diskpart",                                        # diskpart (interactive disk wiper)
    r"Remove-Item\s+.*-Recurse\s+.*-Force.*[Cc]:\\\\",  # PowerShell mass delete on C:\
    r"Format-Volume",                                    # PowerShell format
]

# key persistence: per-provider keys in an owner-only config file
CONFIG_PATH = str(config_dir() / "config.json")

def load_config():
    try:
        with open(CONFIG_PATH) as f:
            c = json.load(f)
            return c if isinstance(c, dict) else {}
    except Exception:
        return {}

def save_config(cfg):
    """Write config (keys + chosen provider) with owner-only perms on POSIX.
    Windows ACLs work differently — the file lives under %APPDATA% which is already
    per-user, so we just write it normally there."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
        if not IS_WIN:
            try:
                os.chmod(CONFIG_PATH, 0o600)
            except Exception:
                pass
        return True
    except Exception:
        return False

def _initial_keys():
    """env var wins per provider, else the saved config."""
    saved = load_config().get("keys", {})
    keys = {}
    for pid, p in PROVIDERS.items():
        keys[pid] = os.environ.get(p["env"], "").strip() or (saved.get(pid) or "").strip()
    return keys

# session state: per-provider keys + the currently selected provider + chosen model per provider
STATE = {
    "keys": _initial_keys(),
    "provider": load_config().get("provider") or DEFAULT_PROVIDER,
    "models": load_config().get("models", {}),   # {provider_id: chosen_model}
}

def persist_state():
    return save_config({"keys": STATE["keys"], "provider": STATE["provider"],
                        "models": STATE["models"]})

# --------------------------------------------------------------------------
# LIVE MODEL CATALOG  -- ask each provider what YOUR key can actually call
# --------------------------------------------------------------------------
# Cache of {provider_id: [model_id, ...]} fetched from each provider's /models
# endpoint. Avoids the whole class of "model unavailable on your plan" errors that
# come from hardcoded names drifting out of date.
_MODEL_CACHE = {}

# Some providers run multiple regional API hosts whose keys are NOT interchangeable
# (a key from one returns 401 on the other). SiliconFlow is the prime example:
# .com (international) vs .cn (China). We try the configured host first, then the
# alternates, and REMEMBER whichever host accepted the key so every later call uses
# it. This makes "which site was my key from?" a non-issue for the user.
HOST_ALIASES = {
    "siliconflow": ["api.siliconflow.com", "api.siliconflow.cn"],
}
# {provider_id: working_host} once discovered for the current key
_HOST_OK = {}

def _provider_urls(provider_id):
    """Yield (chat_url, models_url) candidates for a provider, best-known host first."""
    prov = PROVIDERS[provider_id]
    base_chat = prov["url"]
    base_models = prov.get("models_url", "")
    aliases = HOST_ALIASES.get(provider_id)
    if not aliases:
        yield base_chat, base_models
        return
    # if we already know which host works for this key, use only that
    known = _HOST_OK.get(provider_id)
    hosts = [known] + [h for h in aliases if h != known] if known else list(aliases)
    # derive the host currently in base_chat so we can swap it
    cur_host = re.sub(r"^https?://([^/]+)/.*$", r"\1", base_chat)
    for h in hosts:
        yield (base_chat.replace(cur_host, h, 1),
               base_models.replace(cur_host, h, 1) if base_models else "")

# crude size ranking so "biggest first" still roughly holds for an unknown catalog
def _model_rank(mid):
    s = mid.lower()
    score = 0
    # explicit param-count hints
    m = re.search(r"(\d+)\s*b\b", s) or re.search(r"-(\d+)b", s)
    if m:
        try: score += int(m.group(1))
        except Exception: pass
    # qualitative hints when there's no number
    for kw, pts in (("pro", 300), ("max", 320), ("ultra", 340), ("405", 405), ("671", 671),
                    ("flagship", 350), ("large", 200), ("70", 70), ("32", 32),
                    ("coder", 40), ("instruct", 10),
                    ("flash", -20), ("mini", -40), ("lite", -45), ("small", -50),
                    ("8b", 8), ("7b", 7), ("3b", 3), ("1.5", -10)):
        if kw in s: score += pts
    # generation/version bonus: a newer major version of the same family should sort
    # first (e.g. deepseek-v4-* above deepseek-v3, gemini-2.5 above gemini-1.5). Weighted
    # heavily enough that a newer generation beats an older one even when the newer is a
    # "flash"/"mini" variant (which otherwise carries a size penalty above).
    vm = re.search(r"v(\d+)\b", s) or re.search(r"-(\d+)\.(\d+)", s)
    if vm:
        try: score += int(vm.group(1)) * 25
        except Exception: pass
    return score

def fetch_models(provider_id, force=False):
    """Fetch the live list of chat models a provider exposes to this key.
    Returns {"models": [...], "source": "live"|"fallback"|"error", "error": ...}."""
    prov = PROVIDERS.get(provider_id)
    if not prov:
        return {"models": [], "source": "error", "error": "unknown provider"}
    if not force and _MODEL_CACHE.get(provider_id):
        return {"models": _MODEL_CACHE[provider_id], "source": "live"}
    key = STATE.get("keys", {}).get(provider_id, "")
    if not key:
        return {"models": list(prov["models"]), "source": "fallback", "error": "no key yet"}

    last_err = None
    # try each candidate host (e.g. SiliconFlow .com then .cn) until one accepts the key
    for chat_url, models_url in _provider_urls(provider_id):
        if not models_url:
            continue
        host = re.sub(r"^https?://([^/]+)/.*$", r"\1", models_url)
        try:
            req = urllib.request.Request(models_url, headers={
                "Authorization": "Bearer " + key,
                "User-Agent": f"thedawg/{__version__}",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode())
            items = data.get("data", data if isinstance(data, list) else [])
            ids = []
            for it in items:
                mid = it.get("id") if isinstance(it, dict) else str(it)
                if not mid:
                    continue
                low = mid.lower()
                # keep chat/text LLMs only; drop embeddings/rerank/image/audio/video/tts/etc.
                if any(b in low for b in ("embed", "rerank", "bge-", "whisper", "tts", "stt",
                                          "stable-diffusion", "flux", "sdxl", "kolors", "cogvideo",
                                          "wan-", "speech", "audio", "image", "video", "vl-",
                                          "-vl", "vision", "ocr")):
                    continue
                ids.append(mid)
            if not ids:
                last_err = "no chat models returned"
                continue
            ids = sorted(set(ids), key=_model_rank, reverse=True)
            _MODEL_CACHE[provider_id] = ids
            if provider_id in HOST_ALIASES:
                _HOST_OK[provider_id] = host   # remember the host that worked for this key
            return {"models": ids, "source": "live", "host": host}
        except urllib.error.HTTPError as e:
            detail = ""
            try: detail = e.read().decode(errors="replace")[:150]
            except Exception: pass
            if e.code == 401:
                last_err = "key rejected (401)"
                continue   # try the next host — a .cn key 401s on .com and vice versa
            if e.code == 403:
                last_err = "forbidden (403): " + detail
                continue
            last_err = f"HTTP {e.code}" + (": "+detail if detail else "")
        except Exception as e:
            last_err = str(e)

    # nothing worked → fall back to the static list, with a clear reason
    hint = ""
    if provider_id in HOST_ALIASES and last_err and "401" in last_err:
        hint = (" — the key was rejected on every SiliconFlow host (.com and .cn). "
                "Re-copy the key (watch for spaces), or check the account needs verification.")
    return {"models": list(prov["models"]), "source": "fallback",
            "error": (last_err or "could not reach provider") + hint}

def provider_model_chain(provider_id):
    """The model order to try: live catalog if we have it, else the static fallback."""
    return _MODEL_CACHE.get(provider_id) or list(PROVIDERS[provider_id]["models"])

# --------------------------------------------------------------------------
# TOOL LIBRARY  -- persistent, reloadable tools (code + conversation)
# --------------------------------------------------------------------------
LIBRARY_DIR = str(app_data_dir() / "library")

def _safe_id(name):
    return re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "tool")).strip("_") or "tool"

def library_save(name, code, messages, version="testing", args="", sid=None,
                 ver="1.0.0", named=False, title=""):
    """Snapshot a tool to the library at its CURRENT state: its code, the full build
    conversation, the version badge, and the test args. Reopening it restores all of
    that so you continue exactly where you left off — like saving a chat."""
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    tid = _safe_id(name)
    rec = {"id": tid, "name": name or tid, "code": code,
           "messages": messages or [], "version": version or "testing",
           "args": args or "", "toolkit": (detect_toolkit(code or "") or {}).get("label"),
           "ver": ver or "1.0.0", "named": bool(named), "title": title or (name or tid),
           "from_session": sid, "saved": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(LIBRARY_DIR, tid + ".json"), "w") as f:
        json.dump(rec, f)
    return {"id": tid, "saved": rec["saved"]}

def library_list():
    if not os.path.isdir(LIBRARY_DIR):
        return {"tools": []}
    tools = []
    for fn in os.listdir(LIBRARY_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(LIBRARY_DIR, fn)) as f:
                r = json.load(f)
            tools.append({"id": r.get("id"), "name": r.get("name"),
                          "saved": r.get("saved"), "toolkit": r.get("toolkit"),
                          "version": r.get("version", "testing"),
                          "ver": r.get("ver", "1.0.0"), "title": r.get("title", r.get("name")),
                          "lines": len((r.get("code") or "").splitlines())})
        except Exception:
            continue
    tools.sort(key=lambda t: t.get("saved", ""), reverse=True)
    return {"tools": tools}

def library_load(tid):
    path = os.path.join(LIBRARY_DIR, _safe_id(tid) + ".json")
    if not os.path.exists(path):
        return {"error": "not found"}
    with open(path) as f:
        return {"tool": json.load(f)}

def library_delete(tid):
    path = os.path.join(LIBRARY_DIR, _safe_id(tid) + ".json")
    try:
        os.remove(path); return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

# --------------------------------------------------------------------------
# SESSIONS  -- live works-in-progress (auto-saved as you build), like chats
# --------------------------------------------------------------------------
SESSION_DIR = str(app_data_dir() / "sessions")

def session_save(sid, name, code, messages, version="testing", args="",
                 ver="1.0.0", named=False, title=""):
    """Auto-save the live conversation+code for a tool in progress (its full state)."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    sid = sid or time.strftime("s%Y%m%d-%H%M%S")
    rec = {"id": sid, "name": name or "untitled", "code": code or "",
           "messages": messages or [], "version": version or "testing", "args": args or "",
           "toolkit": (detect_toolkit(code or "") or {}).get("label"),
           "ver": ver or "1.0.0", "named": bool(named), "title": title or (name or "untitled"),
           "updated": time.strftime("%Y-%m-%d %H:%M")}
    with open(os.path.join(SESSION_DIR, _safe_id(sid) + ".json"), "w") as f:
        json.dump(rec, f)
    return {"id": sid, "updated": rec["updated"]}

def session_list():
    if not os.path.isdir(SESSION_DIR):
        return {"sessions": []}
    out = []
    for fn in os.listdir(SESSION_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSION_DIR, fn)) as f:
                r = json.load(f)
            msgs = r.get("messages", [])
            out.append({"id": r.get("id"), "name": r.get("name"),
                        "updated": r.get("updated"), "toolkit": r.get("toolkit"),
                        "turns": sum(1 for m in msgs if m.get("role") == "user"),
                        "hasCode": bool(r.get("code"))})
        except Exception:
            continue
    out.sort(key=lambda s: s.get("updated", ""), reverse=True)
    return {"sessions": out}

def session_load(sid):
    path = os.path.join(SESSION_DIR, _safe_id(sid) + ".json")
    if not os.path.exists(path):
        return {"error": "not found"}
    with open(path) as f:
        return {"session": json.load(f)}

def session_delete(sid):
    try:
        os.remove(os.path.join(SESSION_DIR, _safe_id(sid) + ".json")); return {"ok": True}
    except Exception as e:
        return {"error": str(e)}

# --------------------------------------------------------------------------
# GUI TOOLKITS  -- detect which windowing toolkit a tool uses, and how to get it
# --------------------------------------------------------------------------
# Maps a top-level import to (human label, pip package name, logical system packages).
# The system-package hint is resolved to THIS distro's real names via install_line(), so
# on CachyOS you get `sudo pacman -S --needed tk`, not a Debian package that doesn't exist.
# GTK4/PyGObject has no usable pip wheel — it can ONLY come from the distro.
GUI_TOOLKITS = {
    "tkinter":       ("Tkinter",       None,            ("tk",)),
    "customtkinter": ("CustomTkinter", "customtkinter", ("tk",)),   # needs Tk under the hood
    "PyQt5":         ("PyQt5",         "PyQt5",         ()),
    "PyQt6":         ("PyQt6",         "PyQt6",         ("pyqt6",)),
    "PySide6":       ("PySide6",       "PySide6",       ("pyside6",)),
    "PySide2":       ("PySide2",       "PySide2",       ()),
    "gi":            ("GTK4 / PyGObject", None,         ("pygobject", "gtk4", "libadwaita")),
    "wx":            ("wxPython",      "wxPython",      ()),
}

def detect_toolkit(code):
    """Return the GUI toolkit a tool uses, or None.
    Result shape: {module, label, pip (pip package or None), sys_hint (distro command)}."""
    tops = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", code, re.M):
        tops.add(m.group(1).split(".")[0])
    # order matters: check the explicit toolkits before the generic `gi` binding
    for mod in ("PyQt6", "PySide6", "PyQt5", "PySide2", "customtkinter", "wx", "gi", "tkinter"):
        if mod in tops:
            label, pipname, syspkgs = GUI_TOOLKITS[mod]
            return {"module": mod, "label": label, "pip": pipname,
                    "sys_hint": install_line(*syspkgs) if syspkgs else None,
                    # kept for backwards compatibility with saved sessions
                    "apt_hint": install_line(*syspkgs) if syspkgs else None}
    return None

# --------------------------------------------------------------------------
# DEPENDENCIES  -- detect third-party imports, optionally install into a venv
# --------------------------------------------------------------------------
def detect_deps(code):
    """Return third-party pip deps + the GUI toolkit pip package (if any).
    On TheDawg EVERYTHING — including the GUI toolkit — installs via pip, so the
    UI just needs one unified install button. Tkinter is stdlib so it has no pip
    package, but on Linux it may need an apt hint."""
    std = getattr(sys, "stdlib_module_names", set())
    obvious = {"os","sys","re","io","json","time","math","socket","subprocess","argparse",
               "itertools","collections","random","hashlib","base64","struct","threading",
               "datetime","pathlib","shutil","csv","urllib","textwrap","glob","tempfile",
               "functools","typing","enum","dataclasses","queue","signal","select","ssl",
               "ipaddress","binascii","zlib","gzip","sqlite3","html","xml","http","email",
               "platform","tkinter"}
    toolkit_mods = set(GUI_TOOLKITS.keys()) | {"gi"}
    pip = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", code, re.M):
        top = m.group(1).split(".")[0]
        if (top and top not in std and top not in obvious
                and top not in toolkit_mods and not top.startswith("_")):
            pip.add(top)
    tk = detect_toolkit(code)
    # roll the toolkit's pip name into the pip list, so one click installs everything
    if tk and tk.get("pip"):
        pip.add(tk["pip"])
    return {"pip": sorted(pip), "toolkit": tk}


VENV_DIR = str(app_data_dir() / "venv")

def _venv_python():
    """Return the python interpreter inside our managed venv, or None if not built yet.
    Windows lives in Scripts/python.exe; POSIX in bin/python."""
    cands = [Path(VENV_DIR) / "Scripts" / "python.exe",
             Path(VENV_DIR) / "bin" / "python",
             Path(VENV_DIR) / "bin" / "python3"]
    for c in cands:
        if c.exists():
            return str(c)
    return None

def install_deps(pkgs):
    """Install pip packages into TheDawg's managed venv. Returns log + python path.

    The venv is created WITH access to system site-packages so a tool can use BOTH
    pip packages installed here AND anything Python already has on this machine —
    which matters on Arch/CachyOS, where PyQt6, Pillow and friends are usually
    already present as native packages and re-downloading them is pure waste.

    Two speedups over the old behaviour:
      * `uv` is used when it's on PATH. It resolves and installs an order of
        magnitude faster than pip, and it's a single static binary a lot of Arch
        users already have.
      * `--upgrade` is gone. It forced a PyPI round-trip for every package on
        every click, even when the package was already installed and fine.
    """
    if not pkgs:
        return {"ok": True, "log": "no pip packages to install — already covered", "python": sys.executable}
    try:
        if not os.path.isdir(VENV_DIR):
            import venv
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(VENV_DIR)
        vpy = _venv_python() or sys.executable
        uv = shutil.which("uv")
        if uv:
            cmd = [uv, "pip", "install", "--python", vpy, *pkgs]
        else:
            cmd = [vpy, "-m", "pip", "install", "--disable-pip-version-check",
                   "--no-input", *pkgs]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
        if uv:
            out = f"[using uv — {uv}]\n" + out
        return {"ok": proc.returncode == 0, "log": out[-1800:], "python": vpy}
    except Exception as e:
        return {"ok": False, "log": f"venv/install failed: {e}", "python": sys.executable}

# the interpreter used to run a generated tool. We prefer the managed venv (which sees
# the system site-packages too, so it has everything available). If the venv hasn't
# been built yet, we fall back to the interpreter TheDawg itself is running on.
def run_python(code=None):
    return _venv_python() or sys.executable

# ==========================================================================
# helpers
# ==========================================================================
def looks_dangerous(code):
    return [p for p in DANGER if re.search(p, code)]

def _http_post(url, headers, body, timeout=120):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())

# --------------------------------------------------------------------------
# CONTEXT BUDGET  -- keep requests under the ACTIVE model's real window
# --------------------------------------------------------------------------
# The previous version used one fixed budget (120k chars). That was the bug behind
# "works on a fresh tool, dies after long use": a long session would fall through
# the model chain to a SMALL-context model (e.g. an 8k-token model) for which 120k
# chars is wildly over the limit — so the request 400'd even though trimming "ran".
# Now we budget against the specific model being called.
#
# Context windows in TOKENS (input side). ~3.5 chars/token for code-heavy text, and
# we reserve room for the reply, so usable input chars ≈ tokens * 3. Unknown models
# get a conservative default so we never overshoot a small one.
MODEL_CONTEXT_TOKENS = {
    # Groq
    "llama-3.3-70b-versatile": 128000, "openai/gpt-oss-120b": 128000,
    "openai/gpt-oss-20b": 128000, "gemma2-9b-it": 8192, "llama-3.1-8b-instant": 128000,
    # SiliconFlow
    "deepseek-ai/deepseek-v3": 64000, "qwen/qwen2.5-72b-instruct": 32000,
    "qwen/qwen2.5-coder-32b-instruct": 32000, "deepseek-ai/deepseek-v2.5": 32000,
    "qwen/qwen2.5-7b-instruct": 32000,
    # Google
    "gemini-2.5-pro": 1000000, "gemini-2.5-flash": 1000000, "gemini-2.0-flash": 1000000,
    "gemini-1.5-pro": 2000000, "gemini-1.5-flash": 1000000,
    # Novita
    "deepseek/deepseek-v3": 64000, "qwen/qwen-2.5-72b-instruct": 32000,
    "meta-llama/llama-3.1-70b-instruct": 128000, "meta-llama/llama-3.1-8b-instruct": 128000,
    # DeepSeek (first-party) — V4 Pro and Flash both carry a 1M-token window
    # DeepSeek V4 context windows (1M-token), as exposed via SiliconFlow / Novita
    "deepseek-ai/deepseek-v4-flash": 1000000, "deepseek-ai/deepseek-v4-pro": 1000000,
    "deepseek/deepseek-v4-flash": 1000000, "deepseek/deepseek-v4-pro": 1000000,
}
DEFAULT_CONTEXT_TOKENS = 16000      # safe assumption for an unknown model
REPLY_RESERVE_TOKENS   = 4000       # leave room for the model's answer

def context_budget_chars(model):
    """Usable input-char budget for a specific model, conservatively converted from
    its token window with headroom reserved for the reply."""
    toks = MODEL_CONTEXT_TOKENS.get((model or "").lower(), DEFAULT_CONTEXT_TOKENS)
    usable = max(2000, toks - REPLY_RESERVE_TOKENS)
    # ~3 input chars per token (conservative for code), capped so we never send an
    # absurdly huge request even to a million-token model (keeps latency/cost sane).
    # The cap is generous enough that a large tool plus a long build conversation
    # survives on a big-window model (e.g. DeepSeek V4 Flash / Gemini) instead of
    # being trimmed prematurely, but still bounds latency and token spend.
    return min(usable * 3, 600_000)

def _msg_len(m):
    return len(m.get("content", "") or "")

# matches a fenced code block so we can collapse superseded copies
_CODE_FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\n.*?```", re.S)

def trim_history(messages, model=None):
    """Keep a long build conversation under the ACTIVE model's window without losing
    what matters. Two-stage:
      1. COLLAPSE every OLD assistant code block into a one-line placeholder — only the
         most recent full script is kept verbatim. (This is the real fix: long sessions
         accumulate many full copies of the same growing program, and that redundancy,
         not the chat, is what blows the context window.)
      2. If still over budget, drop the stale middle of the conversation, keeping the
         system prompt, the current code, and the most recent turns; leave a marker.
    """
    if not messages:
        return messages
    budget_total = context_budget_chars(model)

    system = [m for m in messages if m.get("role") == "system"]
    body   = [m for m in messages if m.get("role") != "system"]

    # ---- stage 1: collapse superseded code blocks ----
    last_code_idx = None
    for i in range(len(body) - 1, -1, -1):
        if body[i].get("role") == "assistant" and "```" in (body[i].get("content") or ""):
            last_code_idx = i
            break
    if last_code_idx is not None:
        for i in range(len(body)):
            if i == last_code_idx:
                continue
            m = body[i]
            if m.get("role") == "assistant" and "```" in (m.get("content") or ""):
                collapsed = _CODE_FENCE.sub("`[earlier version of the code — superseded by the latest below]`",
                                            m["content"])
                body[i] = {"role": m["role"], "content": collapsed}

    sys_len = sum(_msg_len(m) for m in system)
    budget  = budget_total - sys_len
    total   = sum(_msg_len(m) for m in body)
    if total <= budget:
        return system + body   # stage-1 collapse alone got us under the limit

    # ---- stage 2: drop the stale middle, force-keeping the current code ----
    # recompute the code index after collapse (it didn't move)
    kept_tail, used = [], 0
    for i in range(len(body) - 1, -1, -1):
        m = body[i]
        L = _msg_len(m)
        if used + L <= budget or not kept_tail:
            kept_tail.append(m); used += L
        elif i == last_code_idx:
            content = m.get("content") or ""
            if L > budget:
                content = content[: max(2000, budget - 200)] + "\n# …(truncated by TheDawg to fit this model)…"
            kept_tail.append({"role": m["role"], "content": content}); used += min(L, budget)
        else:
            continue
    kept_tail.reverse()

    dropped = len(body) - len(kept_tail)
    marker = []
    if dropped > 0:
        marker = [{"role": "user", "content":
                   f"(TheDawg note: {dropped} earlier message(s) were trimmed to fit this model's "
                   f"context window. The current code and recent discussion are below; treat the "
                   f"latest code block as the source of truth.)"}]
    result = system + marker + kept_tail

    # ---- stage 3: HARD GUARANTEE — never exceed budget, even by one char ----
    # Stages 1-2 can land slightly over (the newest message is kept whole, the system
    # prompt is large, etc.). That residual overflow was the real cause of the 400 that
    # struck only after long use. Here we make overflow impossible: while the payload is
    # over the model's total budget, truncate the single largest NON-system message (the
    # current code, almost always) until everything fits with headroom.
    def _total(ms): return sum(_msg_len(m) for m in ms)
    guard = 0
    while _total(result) > budget_total and guard < 200:
        guard += 1
        # find the largest message that isn't a system message
        idx, biggest = -1, -1
        for i, m in enumerate(result):
            if m.get("role") == "system":
                continue
            L = _msg_len(m)
            if L > biggest:
                biggest, idx = L, i
        if idx < 0 or biggest <= 0:
            break
        over = _total(result) - budget_total
        # cut the overflow plus a small margin, but keep at least a stub
        keep_len = max(500, _msg_len(result[idx]) - over - 400)
        c = result[idx]["content"]
        if keep_len >= len(c):
            break
        result[idx] = {"role": result[idx]["role"],
                       "content": c[:keep_len] + "\n…(truncated by TheDawg to fit this model's context)…"}
    return result

def call_model(messages, provider_id=None, temperature=0.3, _fallback_chain=None):
    """Call the selected provider, falling through its model chain on error.
    Returns {"reply", "model", "provider"} or {"error"}.
    `temperature` defaults to 0.3; the code-build path lowers it for determinism.
    If the whole provider chain fails AND a key exists for a configured fallback
    provider (e.g. Groq behind SiliconFlow), the call is retried there once so a
    SiliconFlow outage or quota stop doesn't dead-end the build."""
    pid = provider_id or STATE.get("provider") or DEFAULT_PROVIDER
    prov = PROVIDERS.get(pid)
    if not prov:
        return {"error": f"Unknown provider '{pid}'."}
    key = STATE.get("keys", {}).get(pid, "")
    # compute fallback providers up front so even a missing key can fall through.
    if _fallback_chain is None:
        _fallback_chain = [p for p in FALLBACK_PROVIDERS
                           if p != pid and STATE.get("keys", {}).get(p)]
    if not key:
        if _fallback_chain:
            nxt_pid, rest = _fallback_chain[0], _fallback_chain[1:]
            alt = call_model(messages, nxt_pid, temperature, _fallback_chain=rest)
            if not alt.get("error"):
                alt["fellback_from"] = pid
                return alt
        return {"error": f"No API key for {prov['label']}. Add it in Settings, "
                         f"or set {prov['env']} and restart."}

    # raw history; trimmed PER MODEL inside the loop (each model has its own window)
    raw_messages = messages

    # model order: a user-chosen model wins; otherwise fall back to this provider's
    # configured default (e.g. DeepSeek V4 Flash on SiliconFlow) so the primary model
    # is honoured even though the live catalog is rank-sorted (which would otherwise
    # float the pricier V4 Pro to the top). Whatever we pick is pinned to the front.
    chosen = STATE.get("models", {}).get(pid) or DEFAULT_MODEL_BY_PROVIDER.get(pid)
    chain = provider_model_chain(pid)
    if chosen:
        # match case-insensitively against the live chain so a slightly different
        # capitalisation from the catalog doesn't create a duplicate entry.
        cl = chosen.lower()
        chain = [chosen] + [m for m in chain if m.lower() != cl]

    # which host to call: the one fetch_models proved works for this key, else the
    # configured one. (Handles SiliconFlow .com vs .cn automatically.)
    chat_url = prov["url"]
    for cu, _mu in _provider_urls(pid):
        chat_url = cu
        break

    last = None
    context_hit = False
    _retried_host = [False]   # one-shot host re-discovery guard (mutable for closure-free use)
    for model in chain:
        # trim to THIS model's context window — the fix for "dies after long use":
        # a small-context model deeper in the chain now gets a request sized for it.
        messages = trim_history(raw_messages, model)
        # pre-flight: if even the trimmed payload won't fit this model (e.g. system
        # prompt + current code alone exceeds a tiny 8k window), skip it instead of
        # sending a request we know will 400. A bigger model later in the chain may fit.
        if sum(_msg_len(m) for m in messages) > context_budget_chars(model):
            last = f"{model}: skipped (payload exceeds its context window)"
            context_hit = True
            continue
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
                "User-Agent": f"thedawg/{__version__}",
                "Accept": "application/json",
            }
            body = {"model": model, "temperature": temperature, "messages": messages}
            data = _http_post(chat_url, headers, body)
            reply = data["choices"][0]["message"]["content"]
            return {"reply": reply, "model": model, "provider": pid}
        except urllib.error.HTTPError as e:
            detail = ""
            try: detail = e.read().decode(errors="replace")[:400]
            except Exception: pass
            low = detail.lower()
            # --- the conversation got too big for this model's context window ---
            if (e.code in (400, 413) and any(s in low for s in (
                    "context", "token", "maximum context", "too long", "context_length",
                    "context length", "max_tokens", "reduce the length", "input is too long"))):
                context_hit = True
                last = f"{model}: context-window limit"
                continue   # a smaller-context sibling won't help, but try in case limits differ
            if e.code == 403 and "1010" in detail:
                return {"error": f"Blocked by Cloudflare (403/1010) before reaching "
                                 f"{prov['label']}. Usually a VPN/proxy or outdated client, not your key."}
            if e.code == 401:
                # For a multi-host provider (SiliconFlow .com/.cn), a 401 may just mean
                # we're hitting the wrong regional host for this key. Discover the right
                # one and retry this same request once.
                if pid in HOST_ALIASES and not _retried_host[0]:
                    _retried_host[0] = True
                    probe = fetch_models(pid, force=True)
                    if probe.get("source") == "live" and _HOST_OK.get(pid):
                        new_url = None
                        for cu, _mu in _provider_urls(pid):
                            new_url = cu; break
                        if new_url and new_url != chat_url:
                            chat_url = new_url
                            # retry the very same model against the correct host
                            try:
                                body = {"model": model, "temperature": temperature, "messages": messages}
                                data = _http_post(chat_url, headers, body)
                                reply = data["choices"][0]["message"]["content"]
                                return {"reply": reply, "model": model, "provider": pid}
                            except Exception as e2:
                                last = f"{model}: retry on {_HOST_OK[pid]} failed: {e2}"
                                continue
                return {"error": f"{prov['label']} rejected the key (401). Check it in Settings — "
                                 f"and confirm you're using a {prov['label']} key, not another provider's."
                                 + (" For SiliconFlow, the key must be from the same site as the "
                                    "endpoint (cloud.siliconflow.com \u2194 api.siliconflow.com)."
                                    if pid == "siliconflow" else "")}
            if e.code == 429:
                return {"error": f"{prov['label']} rate-limited this request (429): "
                                 f"{detail or 'slow down or check your quota'}."}
            if e.code in (404, 400):
                # this specific model name isn't callable with your key — try the next
                last = f"{model}: HTTP {e.code} (this model isn't available to your {prov['label']} key)"
                continue
            last = f"{model}: HTTP {e.code} {detail}"
        except Exception as e:
            last = f"{model}: {e}"

    def _try_fallback(reason):
        if _fallback_chain:
            nxt_pid, rest = _fallback_chain[0], _fallback_chain[1:]
            alt = call_model(messages, nxt_pid, temperature, _fallback_chain=rest)
            if not alt.get("error"):
                alt["fellback_from"] = pid
                return alt
        return None

    if context_hit:
        return {"error": "context_overflow",
                "detail": "Your current tool plus the build conversation is too large for the "
                          "available model(s). TheDawg already collapses old code revisions and "
                          "trims old turns automatically, so this means the tool itself is now very "
                          "big. Two fixes: pick a larger-context model in Settings (Gemini and the "
                          "70B/120B models have huge windows), or hit ＋ new tool to start fresh — "
                          "your saved work in the library is untouched. You can also save the current "
                          "tool to the library first, then reopen it in a clean session to keep going."}
    alt = _try_fallback(last)
    if alt:
        return alt
    return {"error": f"{prov['label']} chain failed. Last: {last}. "
                     f"Try Settings → refresh models, or pick a different model/provider."}

def extract_code(reply):
    """Pull the python code block out of a model reply (tagged, else any fence)."""
    m = re.search(r"```(?:python|py)\s*\n(.*?)```", reply, re.S | re.I) \
        or re.search(r"```\s*\n(.*?)```", reply, re.S)
    return m.group(1).rstrip() if m else None

def replace_first_code_block(reply, new_code):
    """Swap the body of the first python/py fenced block (or any fenced block) in a
    reply with new_code, preserving the surrounding prose. Mirrors extract_code's
    block selection so the swapped block is the same one the rest of the app reads.
    Returns the rewritten reply, or the original if no fenced block is present."""
    nc = new_code.rstrip()
    def _do(m):
        return m.group(1) + nc + "\n" + m.group(3)
    for pat in (re.compile(r"(```(?:python|py)\s*\n)(.*?)(```)", re.S | re.I),
                re.compile(r"(```\s*\n)(.*?)(```)", re.S)):
        if pat.search(reply):
            return pat.sub(_do, reply, count=1)
    return reply

# --------------------------------------------------------------------------
# WHOLE-CODE ANALYSIS  -- catch clashes the model can't see in its own output
# --------------------------------------------------------------------------
# A model checking its OWN code shares its own blind spots ("correlated error
# modes"), so it can convince itself broken code is fine. An INDEPENDENT analyzer
# breaks that: it reads the file as a whole and flags real problems — undefined
# names, calls with the wrong number of arguments, unused variables, redefinitions,
# unreachable code — BEFORE the tool is ever run. Uses Ruff if it's installed
# (faster, deeper); otherwise falls back to a built-in ast pass so TheDawg stays
# zero-dependency and "just works".

def _ruff_path():
    import shutil as _sh
    return _sh.which("ruff")

def analyze_with_ruff(code):
    """Run Ruff's correctness lints (the F/E9 families: undefined names, bad calls,
    unused vars, syntax) and return a list of issue strings. None if Ruff absent."""
    ruff = _ruff_path()
    if not ruff:
        return None
    fd, path = tempfile.mkstemp(prefix="thedawg_ruff_", suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)
        # F = pyflakes (undefined names, unused imports/vars, redefinitions, f-string bugs)
        # E9 = syntax/runtime-ish errors. We deliberately skip pure-style rules.
        proc = subprocess.run(
            [ruff, "check", "--select", "F,E9", "--output-format", "json", "--no-cache", path],
            capture_output=True, text=True, timeout=20)
        try:
            items = json.loads(proc.stdout or "[]")
        except Exception:
            return None
        out = []
        for it in items:
            loc = it.get("location") or {}
            ln = loc.get("row")
            code_id = it.get("code") or ""
            msg = it.get("message") or ""
            out.append(f"L{ln} {code_id}: {msg}" if ln else f"{code_id}: {msg}")
        return out
    except Exception:
        return None
    finally:
        try: os.unlink(path)
        except Exception: pass

def autofix_with_ruff(code):
    """The 'lint-and-fix' loop every serious AI coding tool runs (aider, etc.):
    if Ruff is present, silently apply its SAFE auto-fixes to generated code before
    the user ever sees it. Only fixes that cannot change behaviour are applied —
    things like a stray unused variable or a redundant f-string prefix — so the
    model never burns a whole fix-round on trivial mechanical cleanup. Import
    removal (F401) and redefinition rewrites (F811) are deliberately EXCLUDED, as
    those can touch import side-effects or intent. Returns (code, [rule_ids fixed]);
    a no-op returning the code unchanged when Ruff is absent or nothing is fixable."""
    ruff = _ruff_path()
    if not ruff:
        return code, []
    fd, path = tempfile.mkstemp(prefix="thedawg_fix_", suffix=".py")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)
        sel = ["--select", "F,E9", "--ignore", "F401,F811", "--no-cache"]
        before = subprocess.run([ruff, "check", *sel, "--output-format", "json", path],
                                capture_output=True, text=True, timeout=20)
        try:
            items = json.loads(before.stdout or "[]")
        except Exception:
            items = []
        fixable = sorted({it.get("code") for it in items
                          if (it.get("fix") or {}).get("applicability") == "safe" and it.get("code")})
        if not fixable:
            return code, []
        subprocess.run([ruff, "check", *sel, "--fix", path], capture_output=True, text=True, timeout=20)
        with open(path) as f:
            fixed = f.read().rstrip()
        # only accept the fix if it still parses (paranoia — ruff safe fixes always do)
        try:
            import ast as _ast; _ast.parse(fixed)
        except SyntaxError:
            return code, []
        return (fixed or code), fixable
    except Exception:
        return code, []
    finally:
        try: os.unlink(path)
        except Exception: pass

def analyze_with_ast(code):
    """Built-in, zero-dependency fallback analyzer. Walks the AST to catch the
    highest-value clashes a model can't see in its own output:
      - use of a name that is bound NOWHERE in the file (typo / hallucinated name)
      - calls to a top-level function with the wrong number of positional args
      - calls to a class's OWN method (self.method(...)) with the wrong arity
      - local variables assigned a side-effect-free value but never used
    Precision over recall: it deliberately over-collects 'bound names' (scope-
    insensitively) so it will essentially never flag a name that is legitimately
    defined somewhere — at the cost of missing a few real bugs. Staying silent on
    correct code matters more here than catching everything, because a false alarm
    makes the model 'fix' code that was already right."""
    import ast, builtins
    issues = []
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"L{e.lineno} syntax: {e.msg}"]

    # ---- collect EVERY name bound anywhere in the module (scope-insensitive) ----
    # If the file does `from x import *` we can't know what it pulls in, so the
    # undefined-name check is skipped entirely rather than risk false positives.
    star_import = False
    bound = set()       # every name assigned / defined / imported / used as a param

    def _bind_target(t):
        # record names bound by an assignment/loop/with target (incl. tuple unpacking)
        if isinstance(t, ast.Name):
            bound.add(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for e in t.elts:
                _bind_target(e)
        elif isinstance(t, ast.Starred):
            _bind_target(t.value)
        # attribute/subscript targets (self.x = …, d[k] = …) bind no bare name

    def _bind_args(a):
        for grp in (getattr(a, "posonlyargs", []), a.args, a.kwonlyargs):
            for arg in grp:
                bound.add(arg.arg)
        if a.vararg: bound.add(a.vararg.arg)
        if a.kwarg:  bound.add(a.kwarg.arg)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == "*":
                    star_import = True
                else:
                    bound.add(a.asname or a.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name); _bind_args(node.args)
        elif isinstance(node, ast.Lambda):
            _bind_args(node.args)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                _bind_target(t)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _bind_target(node.target)
        elif isinstance(node, ast.NamedExpr):                 # walrus  (x := …)
            _bind_target(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_target(node.target)
        elif isinstance(node, ast.comprehension):
            _bind_target(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                _bind_target(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                bound.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                bound.add(n)
        elif node.__class__.__name__ in ("MatchAs", "MatchStar") and getattr(node, "name", None):
            bound.add(node.name)                              # match … as name (3.10+)

    builtin_names = set(dir(builtins)) | {
        "__name__", "__file__", "__doc__", "__builtins__", "__spec__", "__class__",
        "__loader__", "__package__", "__path__", "self", "cls",
    }
    allowed = bound | builtin_names

    # ---- undefined names: a Load-context bare name bound NOWHERE and not built-in ----
    if not star_import:
        seen_undef = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                nm = node.id
                if nm not in allowed and nm not in seen_undef:
                    seen_undef.add(nm)
                    issues.append(f"L{node.lineno} undefined: name '{nm}' is used but never "
                                  f"defined, imported, or built-in (typo or missing definition?)")

    # ---- arity helpers ----
    def _sig(fnnode, drop_first=False):
        a = fnnode.args
        posonly = getattr(a, "posonlyargs", [])
        pos = len(posonly) + len(a.args) - (1 if drop_first else 0)
        ndef = len(a.defaults)
        has_var = a.vararg is not None or a.kwarg is not None or bool(a.kwonlyargs)
        return (max(0, pos - ndef), None if has_var else max(0, pos))

    def _check_call(label, mn, mx, callnode):
        # skip calls using *args/**kwargs — too dynamic to judge
        if any(isinstance(a, ast.Starred) for a in callnode.args) or \
           any(k.arg is None for k in callnode.keywords):
            return
        nargs = len(callnode.args) + len(callnode.keywords)
        ln = getattr(callnode, "lineno", "?")
        if mx is not None and nargs > mx:
            issues.append(f"L{ln} call: {label}() called with {nargs} args but takes at most {mx}")
        elif nargs < mn:
            issues.append(f"L{ln} call: {label}() called with {nargs} args but needs at least {mn}")

    # --- arity: direct calls to an UNDECORATED top-level function by bare name ---
    # (a decorator can change a function's effective signature, so we skip those.)
    func_sigs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.decorator_list:
            func_sigs[node.name] = _sig(node)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in func_sigs:
            mn, mx = func_sigs[node.func.id]
            _check_call(node.func.id, mn, mx, node)

    # --- arity: self.method(...) calls vs methods defined in the SAME class ---
    # We know the real signature regardless of base classes, so this is safe even
    # for tools that subclass Gtk.Window / QWidget / tk.Frame. Decorated methods
    # (static/class/property/custom) are skipped — their call shape can differ.
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        methods = {}
        for b in cls.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and not b.decorator_list:
                methods[b.name] = _sig(b, drop_first=True)
        for node in ast.walk(cls):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                    and node.func.attr in methods):
                mn, mx = methods[node.func.attr]
                _check_call("self." + node.func.attr, mn, mx, node)

    # --- unused local variables (per-function, conservative) ---
    # GUI code constantly assigns the result of a call for its side effects
    # (building a widget, wiring a signal), so flagging those produces noise. We
    # ONLY flag a variable that is unused AND was assigned a plain literal/name
    # (a value with no side effect) — that's far more likely to be a real mistake.
    class UnusedVisitor(ast.NodeVisitor):
        def visit_FunctionDef(self, fn):
            assigned, used, simple = {}, set(), set()
            for n in ast.walk(fn):
                if isinstance(n, ast.Assign):
                    # is the RHS side-effect-free? (literal, name, tuple/list of those)
                    rhs = n.value
                    is_simple = isinstance(rhs, (ast.Constant, ast.Name, ast.Tuple,
                                                 ast.List, ast.Dict, ast.Set))
                    for t in n.targets:
                        if isinstance(t, ast.Name):
                            assigned.setdefault(t.id, t.lineno)
                            if is_simple:
                                simple.add(t.id)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                    used.add(n.id)
                elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    used.add(n.target.id)
            for name, ln in assigned.items():
                if name == "_" or name.startswith("_"):
                    continue
                if name not in used and name in simple:
                    issues.append(f"L{ln} unused: local variable '{name}' assigned but never used")
            self.generic_visit(fn)
    UnusedVisitor().visit(tree)

    # --- self.<attr> read but never assigned anywhere in the SAME class ---
    # Runs as a shared helper so it also supplements Ruff (which doesn't catch this).
    if not star_import:
        issues.extend(_unassigned_self_attrs(tree))
    # --- high-confidence quality findings (silent except: pass, shell injection) ---
    issues.extend(_extra_safety_findings(tree))

    # de-dup and cap so we never flood the model
    seen, uniq = set(), []
    for i in issues:
        if i not in seen:
            seen.add(i); uniq.append(i)
    return uniq[:25]

def _unassigned_self_attrs(tree):
    """The #1 runtime crash the import-safe smoke test can NEVER catch: a callback or
    thread reads self.something that no method ever set, so the window opens fine and
    then throws AttributeError the moment the user clicks. Flag only the high-confidence
    case; bail out of a class entirely if it does anything dynamic (setattr/getattr,
    __getattr__/__setattr__) that could create attributes we can't see statically.
    Returns a list of issue strings (possibly empty). Caller handles de-dup."""
    import ast
    out = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        # CRITICAL false-positive guard: a class that subclasses anything (QMainWindow,
        # tk.Frame, QWidget, a project base class, etc.) inherits attributes and methods
        # we cannot see — self.setWindowTitle, self.pack, self.master are all legitimate
        # there. Flagging them would make the model "fix" correct code, the worst outcome.
        # So we ONLY analyze classes with no bases, or whose only base is `object`. That
        # covers plain controller/state classes while staying silent on every widget
        # subclass. (Decorators or keyword bases like metaclass= also mean: skip.)
        bases_ok = all(isinstance(b, ast.Name) and b.id == "object" for b in cls.bases)
        if cls.bases and not bases_ok:
            continue
        if getattr(cls, "keywords", None) or cls.decorator_list:
            continue
        assigned_attrs, read_attrs = set(), {}
        dynamic = False
        # an augmented assignment (self.x += 1) READS self.x before writing it, so a
        # name that ONLY ever appears as an augassign target was never truly initialized.
        # Collect those targets so a typo'd `self.valeu += 1` is caught.
        augained = {}
        for n in ast.walk(cls):
            if (isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Attribute)
                    and isinstance(n.target.value, ast.Name) and n.target.value.id == "self"):
                augained.setdefault(n.target.attr, n.target.lineno)
        for n in ast.walk(cls):
            if (isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                if isinstance(n.ctx, (ast.Store, ast.Del)):
                    assigned_attrs.add(n.attr)
                elif isinstance(n.ctx, ast.Load):
                    read_attrs.setdefault(n.attr, n.lineno)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id in ("setattr", "getattr", "vars"):
                dynamic = True
        # an attr whose ONLY assignment is an augmented one (self.x += …) was never
        # initialized: treat it as a read of an unassigned attr, not an assignment.
        for attr, ln in augained.items():
            assigned_attrs.discard(attr)
            read_attrs.setdefault(attr, ln)
        if any(m for m in cls.body
               if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
               and m.name in ("__getattr__", "__setattr__", "__getattribute__")):
            dynamic = True
        if dynamic:
            continue
        for m in cls.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                assigned_attrs.add(m.name)
        assigned_attrs |= {"__class__", "__dict__", "__doc__", "__module__"}
        for attr, ln in read_attrs.items():
            if attr not in assigned_attrs:
                out.append(f"L{ln} attribute: self.{attr} is read but never assigned in "
                           f"class '{cls.name}' (AttributeError at runtime — set it in "
                           f"__init__, or fix the name)")
    return out

def _extra_safety_findings(tree):
    """A small set of HIGH-CONFIDENCE quality findings the system prompt explicitly
    forbids, so the model can clean them up. Engine-independent (used by both the Ruff
    and the ast paths). Kept deliberately narrow to avoid flagging correct code:

      1. SILENT FAILURE: a bare `except:` or a broad `except Exception/BaseException:`
         whose body does nothing but `pass` (or `...`). That swallows every error with
         no message — exactly the "it silently did nothing" bug the standards prohibit.
      2. SHELL INJECTION: `subprocess.run/Popen/call/check_output/check_call(..., shell=True)`
         where the command is NOT a constant string (a variable/f-string/concatenation),
         or any `os.system(...)` / `os.popen(...)` with a non-constant argument. Both run
         a string through the shell, so a built-from-input command is an injection risk —
         the standards require a list argv instead.
    Returns a list of issue strings (possibly empty). Caller de-dups."""
    import ast
    out = []
    SHELL_FUNCS = {"run", "Popen", "call", "check_output", "check_call"}
    for n in ast.walk(tree):
        # --- 1. silent except: pass ---
        if isinstance(n, ast.ExceptHandler):
            body = [s for s in n.body if not (isinstance(s, ast.Expr)
                    and isinstance(getattr(s, "value", None), ast.Constant)
                    and isinstance(s.value.value, str))]   # drop a docstring-only line
            only_pass = all(isinstance(s, ast.Pass) for s in body) and len(body) > 0
            if not body:  # body was just a string/ellipsis expression
                only_pass = True
            etype = n.type
            broad = (etype is None
                     or (isinstance(etype, ast.Name) and etype.id in ("Exception", "BaseException")))
            if only_pass and broad:
                ln = getattr(n, "lineno", "?")
                out.append(f"L{ln} silent-failure: a broad 'except: pass' swallows every error "
                           f"with no message (forbidden — surface the failure in the window, or "
                           f"narrow the except and handle it)")
        # --- 2. shell injection ---
        if isinstance(n, ast.Call):
            f = n.func
            # subprocess.<func>(..., shell=True, ...) with non-constant command
            is_subprocess = (isinstance(f, ast.Attribute) and f.attr in SHELL_FUNCS
                             and isinstance(f.value, ast.Name) and f.value.id == "subprocess")
            if is_subprocess:
                shell_true = any(k.arg == "shell" and isinstance(k.value, ast.Constant)
                                 and k.value.value is True for k in n.keywords)
                cmd = n.args[0] if n.args else None
                cmd_const = isinstance(cmd, ast.Constant)
                if shell_true and cmd is not None and not cmd_const:
                    ln = getattr(n, "lineno", "?")
                    out.append(f"L{ln} shell-injection: subprocess.{f.attr}(..., shell=True) with a "
                               f"built command runs it through the shell (injection risk — pass a "
                               f"list argv and drop shell=True)")
            # os.system(x) / os.popen(x) with a non-constant arg
            is_ossys = (isinstance(f, ast.Attribute) and f.attr in ("system", "popen")
                        and isinstance(f.value, ast.Name) and f.value.id == "os")
            if is_ossys:
                cmd = n.args[0] if n.args else None
                if cmd is not None and not isinstance(cmd, ast.Constant):
                    ln = getattr(n, "lineno", "?")
                    out.append(f"L{ln} shell-injection: os.{f.attr}() runs a built string through "
                               f"the shell (injection risk — use subprocess with a list argv)")
    return out

def code_map(code):
    """Build a compact structural map of the current tool: imports, top-level
    functions (with signatures), and classes (with their methods). Given to the
    model before it edits, so it sees the file's shape at a glance and stops
    re-introducing bugs it already fixed or calling things that don't exist.
    Returns a short string, or '' if the code doesn't parse."""
    import ast
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ""

    def sig(fn):
        a = fn.args
        parts = []
        posonly = getattr(a, "posonlyargs", [])
        allpos = posonly + a.args
        ndef = len(a.defaults)
        first_def = len(allpos) - ndef
        for i, arg in enumerate(allpos):
            parts.append(arg.arg + ("=…" if i >= first_def else ""))
        if a.vararg: parts.append("*" + a.vararg.arg)
        for kw in a.kwonlyargs: parts.append(kw.arg + "=…")
        if a.kwarg: parts.append("**" + a.kwarg.arg)
        return f"{fn.name}({', '.join(parts)})"

    imports, funcs, classes = [], [], []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports += [a.asname or a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imports += [f"{mod}.{a.name}" for a in node.names]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append(sig(node))
        elif isinstance(node, ast.ClassDef):
            methods = [sig(b) for b in node.body
                       if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            head = node.name + (f"({', '.join(bases)})" if bases else "")
            classes.append((head, methods))

    lines = ["STRUCTURE OF THE CURRENT TOOL (for your reference — keep calls consistent with this):"]
    if imports:
        lines.append("imports: " + ", ".join(imports[:30]))
    for head, methods in classes:
        lines.append(f"class {head}:")
        for m in methods:
            lines.append(f"    {m}")
    if funcs:
        lines.append("functions: " + "; ".join(funcs))
    return "\n".join(lines)

def analyze_code(code):
    """Whole-code clash analysis. Prefers Ruff, falls back to the ast pass.
    Returns {"issues": [...], "engine": "ruff"|"ast", "clean": bool}."""
    ruff_issues = analyze_with_ruff(code)
    if ruff_issues is not None:
        # Ruff is fast and deep on style/logic but does NOT track instance attributes.
        # Supplement it with our high-confidence self.<attr>-never-assigned pass plus the
        # extra safety findings (silent except: pass, shell injection) so those are caught
        # regardless of which engine runs.
        supplemental = []
        try:
            import ast as _ast
            tree = _ast.parse(code)
            if not any(isinstance(n, _ast.ImportFrom) and any(a.name == "*" for a in n.names)
                       for n in _ast.walk(tree)):
                supplemental = _unassigned_self_attrs(tree)
            supplemental = supplemental + _extra_safety_findings(tree)
        except SyntaxError:
            pass
        merged = ruff_issues + [s for s in supplemental if s not in ruff_issues]
        return {"issues": merged, "engine": "ruff", "clean": not merged}
    ast_issues = analyze_with_ast(code)
    return {"issues": ast_issues, "engine": "ast", "clean": not ast_issues}

def smoke_test(code):
    """Silent quality checks on generated code. Returns (passed, report, checks).
    IMPORTANT: this only checks that the code PARSES and IMPORTS cleanly. It does
    NOT open the window — doing that needs a display and would block. For GUI tools
    it also verifies the code is import-safe (no window opens at import time) and is
    TOLERANT of a headless/toolkit-less test box: a missing display or missing GUI
    typelib is an environment fact here, not a bug in the generated tool. Real
    behaviour is verified by the user pressing Run on their own machine."""
    checks = []
    # 0. completeness. A model under length pressure will happily hand back a
    #    script with "# ... rest of the code unchanged ..." in the middle of it.
    #    That parses fine and imports fine, so every later check passes and the
    #    user gets a broken tool. Catch it first and feed it straight back.
    TRUNC = [
        r"#\s*\.\.\.\s*(?:rest|remaining|the rest|previous|existing|unchanged|same)",
        r"#\s*(?:rest|remainder) of (?:the )?(?:code|file|script|class|method)",
        r"#\s*\(?(?:previous|existing|original|earlier) (?:code|implementation|methods?)",
        r"#\s*(?:code )?unchanged\b",
        r"#\s*same as (?:before|above|previous)",
        r"#\s*\.\.\.\s*$",
        r"<\s*(?:rest of|remaining)[^>]*>",
    ]
    for pat in TRUNC:
        m = re.search(pat, code, re.M | re.I)
        if m:
            line = code[:m.start()].count("\n") + 1
            msg = ("The script is incomplete: line %d is a placeholder (%r) instead of real code. "
                   "Return the ENTIRE file with every function written out in full — no "
                   "\"rest unchanged\" markers, no elisions." % (line, m.group(0).strip()[:60]))
            return False, msg, [("complete", False, msg)]

    # stub bodies presented as working code
    stub = re.search(r"^\s*(?:#\s*)?(?:TODO|FIXME|implement(?:ation)? (?:here|goes here))\b",
                     code, re.M | re.I)
    if stub:
        line = code[:stub.start()].count("\n") + 1
        msg = (f"Line {line} is an unimplemented stub ({stub.group(0).strip()[:50]!r}). "
               "Every function in a tool you hand back must actually do its job — "
               "either implement it or remove the feature.")
        return False, msg, [("complete", False, msg)]
    checks.append(("complete", True, ""))

    # 1. syntax
    try:
        import ast
        ast.parse(code)
        checks.append(("syntax", True, ""))
    except SyntaxError as e:
        return False, f"SyntaxError at line {e.lineno}: {e.msg}", [("syntax", False, str(e))]

    tk = detect_toolkit(code)

    # 1b. import-safety for GUI tools: building/running the GUI must be guarded by
    #     `if __name__ == "__main__":` (or a main() called only there), so importing
    #     the module doesn't try to open a window. Catch the obvious mistake of a
    #     top-level mainloop/run/show call.
    if tk:
        bad = re.search(r"^\s*(?:Gtk\.main\(\)|app\.run\(|window\.show_all\(\)|"
                        r"\w+\.mainloop\(\)|sys\.exit\(\s*app\.exec)", code, re.M)
        if bad and "__main__" not in code:
            msg = ("GUI tool isn't import-safe: it opens/runs the window at module top "
                   "level. Move all window construction and the main loop inside "
                   "`if __name__ == \"__main__\":`.")
            checks.append(("import-safe", False, msg))
            return False, msg, checks
        checks.append(("import-safe", True, ""))

    # 2. import-ability: load the module WITHOUT running its __main__ block.
    fd, path = tempfile.mkstemp(prefix="thedawg_test_", suffix=".py")
    # signatures meaning "this box just can't load the GUI" — never a code bug
    ENV_SIGNS = ("Namespace", "not available", "cannot open display", "could not open display",
                 "couldn't connect to display", "no display name", "Unable to init server",
                 "Gtk couldn't be initialized", "GtkInitError", "QXcbConnection",
                 "qt.qpa.plugin", "no Qt platform plugin", "xcb", "DISPLAY",
                 "_tkinter.TclError", "libGL", "Gdk")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(code)
        harness = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('thedawg_candidate', {path!r})\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "try:\n"
            "    spec.loader.exec_module(mod)\n"
            "except (ModuleNotFoundError, ImportError) as e:\n"
            "    print('DEP_MISSING:' + str(e)); sys.exit(0)\n"
            "except SystemExit as e:\n"
            "    print('TOOLKIT_EXIT:' + str(e)); sys.exit(0)\n"
            "except BaseException as e:\n"
            "    import traceback; tb = traceback.format_exc()\n"
            "    sys.stderr.write(tb)\n"
            "    sys.exit(7)\n"
        )
        try:
            proc = subprocess.run([run_python(), "-c", harness],
                                  capture_output=True, stdin=subprocess.DEVNULL, timeout=20)
            out = proc.stdout.decode("utf-8", errors="replace")
            err = proc.stderr.decode("utf-8", errors="replace")
            blob = out + "\n" + err
            if out.startswith("DEP_MISSING:"):
                note = "needs a package (use the deps button)"
                if tk:
                    hint = tk.get("sys_hint") or (("pip install " + tk["pip"]) if tk.get("pip") else "pip install")
                    note = f"needs the {tk['label']} toolkit — {hint}"
                checks.append(("imports", True, note))
            elif out.startswith("TOOLKIT_EXIT:") or (tk and any(s in blob for s in ENV_SIGNS)):
                # the tool bailed gracefully because the toolkit/display isn't on THIS box,
                # or hit an environment-only error. Structurally fine.
                checks.append(("imports", True, "toolkit/display not present on the test box "
                                                 "(expected — runs on the user's own machine)"))
            elif proc.returncode != 0:
                # a genuine error at import/definition time (NameError, bad default, etc.)
                msg = err.strip()[-500:] or "import failed"
                checks.append(("imports", False, msg))
                return False, msg, checks
            else:
                checks.append(("imports", True, ""))
        except subprocess.TimeoutExpired:
            checks.append(("imports", False, "import timed out (top-level code is blocking — "
                                             "is a window opening at import time?)"))
            return False, "Import timed out — there may be blocking/GUI code at module top level.", checks

        # 3. whole-code analysis: catch clashes the model can't see in its own output
        #    (undefined names, wrong-arity calls, unused vars). Independent of the model.
        analysis = analyze_code(code)
        if analysis["clean"]:
            checks.append(("analysis", True, f"{analysis['engine']}: no issues"))
        else:
            # Treat these as fixable findings: report them so the autotest loop can
            # feed them back, but they don't, by themselves, "fail" a tool that imports
            # fine — some ast findings (e.g. an unused var) are minor. We surface them
            # and let the loop decide. Genuine correctness issues (undefined name, bad
            # call) are worth a fix round.
            serious = [i for i in analysis["issues"]
                       if any(k in i for k in ("undefined", "call:", "attribute:", "F821", "F811",
                                               "F706", "F702", "E9", "syntax"))]
            report = (f"Whole-code analysis ({analysis['engine']}) found:\n  - "
                      + "\n  - ".join(analysis["issues"]))
            if serious:
                checks.append(("analysis", False, report))
                return False, report, checks
            else:
                # only minor findings (e.g. unused vars) — note them, still pass
                checks.append(("analysis", True, f"{analysis['engine']}: minor only — " +
                               "; ".join(analysis["issues"][:5])))
        return True, "", checks
    finally:
        try: os.unlink(path)
        except Exception: pass

# ==========================================================================
# RUNTIME PROBE  -- actually OPEN the GUI, look at it, and report back
# --------------------------------------------------------------------------
# smoke_test() only proves the code parses and imports. It never opens the
# window, so it's blind to the failures that matter most: a window that opens
# then crashes, a window that renders BLANK, a callback that dies on click.
# The probe fills that gap. It opens the tool on a *headless* virtual display
# (Xvfb) so it never disturbs your desktop, waits for it to settle, takes a
# screenshot, optionally pokes it (keyboard + a click), and turns all of that
# into a precise text report the model can read — so "it can see what's wrong"
# without you typing a word. Linux-only; degrades gracefully everywhere else.
PROBE_SETTLE = 2.4          # seconds to let the window come up before looking
PROBE_INTERACT = True       # send a few synthetic events to catch click-crashes
SHOT_PATH = os.path.join(tempfile.gettempdir(), "thedawg_lastshot.png")
LAST_PROBE = {}             # most recent probe result (for /api/shot.png + fix/polish)

def _which(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None

def _free_display():
    import random
    for n in range(99, 130):
        if not os.path.exists(f"/tmp/.X11-unix/X{n}"):
            return n
    return random.randint(300, 900)

def _probe_win_geometry(xdo, env, wid):
    try:
        out = subprocess.run([xdo, "getwindowgeometry", "--shell", wid], env=env,
                             capture_output=True, timeout=5).stdout.decode()
        g = dict(re.findall(r"(\w+)=(-?\d+)", out))
        return (int(g["X"]), int(g["Y"]), int(g["WIDTH"]), int(g["HEIGHT"]))
    except Exception:
        return None

def _largest_window_geom(display):
    """Geometry of the biggest visible named window (the tool itself), or None."""
    xdo = _which("xdotool")
    if not xdo:
        return None
    env = dict(os.environ); env["DISPLAY"] = display
    try:
        ids = subprocess.run([xdo, "search", "--onlyvisible", "--name", "."], env=env,
                             capture_output=True, timeout=5).stdout.decode().split()
    except Exception:
        return None
    best, area = None, 256
    for w in ids:
        g = _probe_win_geometry(xdo, env, w)
        if g and g[2] * g[3] > area:
            best, area = g, g[2] * g[3]
    return best

def _capture_screenshot(display, out_path):
    """Best-effort whole-screen grab via whatever capture tool is installed."""
    env = dict(os.environ); env["DISPLAY"] = display
    attempts = []
    for tool, cmd in (("import", ["import", "-window", "root", out_path]),
                      ("maim", ["maim", out_path]),
                      ("scrot", ["scrot", "-o", out_path]),
                      ("spectacle", ["spectacle", "-b", "-n", "-o", out_path]),
                      ("gnome-screenshot", ["gnome-screenshot", "-f", out_path])):
        p = _which(tool)
        if p:
            c = list(cmd); c[0] = p
            attempts.append(c)
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, env=env, capture_output=True, timeout=20)
            if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 200:
                return True
        except Exception:
            continue
    xwd, conv = _which("xwd"), _which("convert", "magick")
    if xwd and conv:
        try:
            p1 = subprocess.run([xwd, "-root", "-silent"], env=env, capture_output=True, timeout=20)
            if p1.returncode == 0 and p1.stdout:
                p2 = subprocess.run([conv, "xwd:-", out_path], input=p1.stdout,
                                    capture_output=True, timeout=20)
                if p2.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 200:
                    return True
        except Exception:
            pass
    return False

def _crop_to_window(shot_path, geom):
    """Trim the full grab down to just the tool window (cleaner shot + sharper
    blank-detection). Best-effort: on any problem, leave the full grab as-is."""
    if not geom:
        return
    try:
        from PIL import Image
        x, y, w, h = geom
        x = max(0, x); y = max(0, y)
        im = Image.open(shot_path).convert("RGB")
        W, H = im.size
        right = min(W, x + w); bottom = min(H, y + h)
        if right - x < 8 or bottom - y < 8:
            return
        im.crop((x, y, right, bottom)).save(shot_path)
    except Exception:
        return

def _analyze_screenshot(path):
    """Describe what's on screen so a TEXT model can 'see' it: is it blank? what's
    the dominant colour? how busy is it? Catches the classic 'window opens but
    renders nothing' bug, with no vision model required."""
    try:
        from PIL import Image
    except Exception:
        return {"ok": False}
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return {"ok": False}
    import statistics
    W, H = im.size
    small = im.resize((64, 64))
    colors = small.getcolors(64 * 64) or []
    if not colors:
        return {"ok": False}
    total = sum(c for c, _ in colors) or 1
    colors.sort(reverse=True)
    dom_count, dom_rgb = colors[0]
    dom_frac = dom_count / total
    q = small.quantize(colors=64).convert("RGB")
    distinct = len(q.getcolors(64 * 64) or [])
    px = small.load(); lums = []
    for yy in range(0, 64, 4):
        for xx in range(0, 64, 4):
            r, g, b = px[xx, yy]
            lums.append(0.299 * r + 0.587 * g + 0.114 * b)
    spread = statistics.pstdev(lums) if len(lums) > 1 else 0.0
    blank = dom_frac >= 0.985 and distinct <= 3 and spread < 6.0
    return {"ok": True, "w": W, "h": H, "dominant": "#%02x%02x%02x" % dom_rgb,
            "dominant_frac": round(dom_frac, 3), "distinct": distinct,
            "spread": round(spread, 1), "blank": blank}

def _drive_ui(display):
    """Best-effort: focus the tool window and poke it (keyboard + a click at the
    window's true centre) to surface 'crashes when you interact' bugs. Never raises."""
    xdo = _which("xdotool")
    if not xdo:
        return []
    env = dict(os.environ); env["DISPLAY"] = display
    done = []
    try:
        ids = subprocess.run([xdo, "search", "--onlyvisible", "--name", "."], env=env,
                             capture_output=True, timeout=5).stdout.decode().split()
    except Exception:
        ids = []
    wid, area = None, -1
    for w in ids:
        g = _probe_win_geometry(xdo, env, w)
        if g and g[2] * g[3] > area:
            wid, area = w, g[2] * g[3]
    wid = wid or (ids[0] if ids else None)
    if wid:
        for c in (["windowfocus", "--sync", wid], ["windowactivate", "--sync", wid],
                  ["windowraise", wid]):
            try:
                subprocess.run([xdo] + c, env=env, capture_output=True, timeout=5)
            except Exception:
                pass
    for keys in (["key", "--clearmodifiers", "Tab"], ["key", "--clearmodifiers", "Tab"],
                 ["key", "--clearmodifiers", "space"], ["key", "--clearmodifiers", "Return"]):
        try:
            subprocess.run([xdo] + keys, env=env, capture_output=True, timeout=5)
            done.append(" ".join(keys)); time.sleep(0.15)
        except Exception:
            break
    g = _probe_win_geometry(xdo, env, wid) if wid else None
    cx, cy = (g[0] + g[2] // 2, g[1] + g[3] // 2) if g else (640, 450)
    for c in (["mousemove", "--sync", str(cx), str(cy)], ["click", "1"], ["click", "1"]):
        try:
            subprocess.run([xdo] + c, env=env, capture_output=True, timeout=5)
            done.append(" ".join(c)); time.sleep(0.2)
        except Exception:
            break
    return done

def probe_run(code, name="tool", settle=None, interact=None):
    """Open the GUI on a headless virtual display, watch it, screenshot it, and
    report what happened. Stores the result in LAST_PROBE and the image at SHOT_PATH.
    Returns a dict with a ready-to-read 'report' string."""
    if settle is None:
        settle = PROBE_SETTLE
    if interact is None:
        interact = PROBE_INTERACT
    tk = detect_toolkit(code)
    if not IS_LINUX:
        res = {"ran": False, "shot": False, "kind": "not-linux",
               "report": "The runtime probe (open + screenshot the window) is Linux-only. "
                         "On this OS use \u25b6 launch to run the tool yourself."}
        LAST_PROBE.clear(); LAST_PROBE.update(res); return res
    if not tk:
        res = {"ran": False, "shot": False, "kind": "not-gui",
               "report": "This isn't a windowed GUI tool (no GUI toolkit imported), so there's "
                         "no window to screenshot. Use \u25b6 launch to run it and read its output."}
        LAST_PROBE.clear(); LAST_PROBE.update(res); return res

    interp = run_python(code)
    try:
        if os.path.exists(SHOT_PATH):
            os.unlink(SHOT_PATH)
    except Exception:
        pass
    fd, path = tempfile.mkstemp(prefix="thedawg_probe_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(code)
    errf = tempfile.NamedTemporaryFile(prefix="thedawg_probe_err_", suffix=".log", delete=False)

    xvfb = _which("Xvfb"); xv = None; headless = False
    display = os.environ.get("DISPLAY", "")
    if xvfb:
        n = _free_display(); display = f":{n}"
        try:
            xv = subprocess.Popen([xvfb, display, "-screen", "0", "1280x900x24", "-nolisten", "tcp"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            headless = True; time.sleep(0.9)
        except Exception:
            xv = None; display = os.environ.get("DISPLAY", ""); headless = False
    if not display:
        for p in (path, errf.name):
            try: os.unlink(p)
            except Exception: pass
        res = {"ran": False, "shot": False, "kind": "no-display",
               "report": "No display is available and Xvfb isn't installed, so the window can't be "
                         "opened to look at it. Install Xvfb for headless self-tests:\n"
                         "  " + (install_line("xvfb", "xdotool", "imagemagick") or "install xvfb xdotool imagemagick") + "\n"
                         "Or use \u25b6 launch inside your desktop session."}
        LAST_PROBE.clear(); LAST_PROBE.update(res); return res

    env = dict(os.environ); env["DISPLAY"] = display
    env["QT_QPA_PLATFORM"] = "xcb"   # force xcb so Qt tools run under Xvfb
    t0 = time.time()
    try:
        proc = subprocess.Popen([interp, path], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=errf,
                                start_new_session=True, env=env)
    except Exception as e:
        for p in (path, errf.name):
            try: os.unlink(p)
            except Exception: pass
        if xv:
            try: xv.terminate()
            except Exception: pass
        res = {"ran": False, "shot": False, "kind": "launch-fail",
               "report": f"Couldn't launch the tool to probe it: {e}"}
        LAST_PROBE.clear(); LAST_PROBE.update(res); return res

    time.sleep(settle)
    alive = proc.poll() is None
    crashed_on_interact = False; interacted = []
    shot_ok = False; analysis = None
    if alive:
        geom = _largest_window_geom(display)
        shot_ok = _capture_screenshot(display, SHOT_PATH)
        if shot_ok:
            _crop_to_window(SHOT_PATH, geom)
            analysis = _analyze_screenshot(SHOT_PATH)
        if interact:
            interacted = _drive_ui(display); time.sleep(0.7)
            if proc.poll() is not None:
                crashed_on_interact = True; alive = False
            geom = _largest_window_geom(display) or geom
            if _capture_screenshot(display, SHOT_PATH):
                _crop_to_window(SHOT_PATH, geom)
                shot_ok = True; analysis = _analyze_screenshot(SHOT_PATH)
    rc = proc.poll()
    secs = round(time.time() - t0, 2)
    try:
        errf.flush(); errf.close()
        with open(errf.name, "rb") as ef:
            err = ef.read().decode("utf-8", errors="replace")
    except Exception:
        err = ""
    # always close the probe window + Xvfb
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try: proc.wait(timeout=2)
        except Exception: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try: proc.terminate()
        except Exception: pass
    if xv:
        try: xv.terminate()
        except Exception: pass
    for p in (path, errf.name):
        try: os.unlink(p)
        except Exception: pass

    res = {"ran": True, "shot": shot_ok, "alive": alive, "rc": rc, "secs": secs,
           "headless": headless, "toolkit": tk.get("label") if tk else None,
           "stderr": err.strip(), "analysis": analysis, "interacted": interacted,
           "crashed_on_interact": crashed_on_interact}
    res["report"] = render_probe_report(res)
    res["ok"] = bool(alive and (not analysis or not analysis.get("blank")))
    LAST_PROBE.clear(); LAST_PROBE.update(res)
    return res

def render_probe_report(p):
    """Turn a probe result into a precise, model-readable account of what happened."""
    if not p or not p.get("ran"):
        return (p or {}).get("report", "Runtime probe didn't run.")
    L = []
    where = "a headless virtual display (Xvfb)" if p.get("headless") else "the live desktop"
    L.append(f"RUNTIME PROBE — opened the {p.get('toolkit') or 'GUI'} window on {where} "
             f"and watched it for {p.get('secs')}s:")
    if p.get("alive"):
        L.append("\u2022 The window opened and STAYED OPEN through startup (no startup crash).")
    elif p.get("crashed_on_interact"):
        L.append(f"\u2022 The window opened but CRASHED when interacted with (exited {p.get('rc')}). "
                 f"A keypress/click kills it — the failing callback's traceback is below. Fix it.")
    else:
        L.append(f"\u2022 The window FAILED: the process exited with code {p.get('rc')} during or "
                 f"right after startup. It does NOT stay open. Traceback below.")
    a = p.get("analysis") or {}
    if p.get("shot") and a.get("ok"):
        if a.get("blank"):
            L.append(f"\u2022 SCREENSHOT: a BLANK/UNIFORM window ({a.get('w')}x{a.get('h')}px) — "
                     f"essentially one flat colour ({a.get('dominant')} covers "
                     f"{int(a.get('dominant_frac',0)*100)}%). The window exists but NOTHING renders. "
                     f"Usual causes: widgets never added to a layout / never packed or gridded; a "
                     f"paint/draw routine that never runs; a zero or mis-set geometry; or an "
                     f"exception swallowed inside a setup callback. Make the UI actually populate.")
        else:
            L.append(f"\u2022 SCREENSHOT: a populated window ({a.get('w')}x{a.get('h')}px, "
                     f"{a.get('distinct')} distinct colours, dominant {a.get('dominant')} "
                     f"~{int(a.get('dominant_frac',0)*100)}%) — it renders real content.")
    elif p.get("alive") and not p.get("shot"):
        L.append("\u2022 SCREENSHOT: none could be captured on this box (no screenshot tool found; "
                 "install imagemagick/maim/scrot for a picture).")
    if p.get("interacted"):
        L.append(f"\u2022 Synthetic interaction sent: {', '.join(p['interacted'])}.")
    if p.get("stderr"):
        L.append("\n--- the tool's own stderr / traceback ---\n" + p["stderr"][-2500:])
    if p.get("alive") and not (a.get("ok") and a.get("blank")) and not p.get("stderr"):
        L.append("\u2022 Net: it launches clean and renders content. Looks healthy from here.")
    return "\n".join(L)


def _latest_code_in(convo):
    """Find the most recent code block in a conversation (the current tool)."""
    for m in reversed(convo):
        if m.get("role") == "assistant":
            c = extract_code(m.get("content", ""))
            if c:
                return c
    return None

def chat_with_autotest(messages, provider_id=None):
    """Call the model, then silently smoke-test any code it returns, feeding
    failures back for up to AUTOTEST_MAX_ROUNDS before returning to the user."""
    convo = list(messages)

    # FILE MAP (feature #3): if there's already a tool in this conversation and the
    # user is asking for a change, give the model a compact structural map of the
    # current code right before it edits — so it keeps calls consistent with what
    # actually exists and stops re-introducing bugs. Injected as a transient system
    # note (not persisted into the saved conversation).
    existing = _latest_code_in(convo)
    if existing:
        cmap = code_map(existing)
        if cmap:
            # place the map just before the final user turn so it's freshest in context
            insert_at = len(convo)
            for i in range(len(convo) - 1, -1, -1):
                if convo[i].get("role") == "user":
                    insert_at = i
                    break
            convo = convo[:insert_at] + [{"role": "system", "content": cmap}] + convo[insert_at:]

    rounds = []
    # Lower temperature on code generation: more deterministic, fewer hallucinated
    # APIs and careless slips. Reasoning paths (intake/review) keep the default 0.3.
    res = call_model(convo, provider_id, temperature=BUILD_TEMPERATURE)
    if res.get("error"):
        return res

    for attempt in range(AUTOTEST_MAX_ROUNDS + 1):
        code = extract_code(res.get("reply", ""))
        if not code:
            res["autotest"] = {"ran": False, "rounds": rounds}
            # No code means the model spoke or asked rather than built. If it asked the
            # user something, structure those questions into tappable options so the user
            # can answer with a click — the opening-intake experience, on every turn.
            res["followup"] = structure_followup(res.get("reply", ""), convo, provider_id)
            return res
        # lint-and-fix loop: silently apply Ruff's SAFE mechanical fixes so trivial
        # cleanup (a stray unused var, a redundant f-string prefix) never costs a fix
        # round. Behaviour-affecting fixes are excluded; see autofix_with_ruff().
        fixed, applied = autofix_with_ruff(code)
        if applied and fixed != code:
            res["reply"] = replace_first_code_block(res.get("reply", ""), fixed)
            code = fixed
        passed, report, checks = smoke_test(code)
        # also surface any non-fatal analysis notes (minor findings) for visibility
        minor = [note for name, ok, note in checks if name == "analysis" and ok and note
                 and ("minor only" in note)]
        rounds.append({"attempt": attempt + 1, "passed": passed,
                       "checks": [c[0] for c in checks if c[1]],
                       "failed": [c[0] for c in checks if not c[1]],
                       "report": "" if passed else report,
                       "autofixed": applied,
                       "minor": minor})
        if passed or attempt == AUTOTEST_MAX_ROUNDS:
            res["autotest"] = {"ran": True, "passed": passed, "rounds": rounds}
            return res
        # FEED THE FAILURE BACK with a structural map so the fix is informed, not blind.
        # Giving the model a map of its own code + the exact analyzer findings produces a
        # far better fix than just "it failed, try again" (the agentic-loop pattern).
        cmap = code_map(code)
        fix_msg = (f"Your code failed an automatic quality check before I saw it. "
                   f"Fix the SPECIFIC problems below and return the FULL corrected script "
                   f"(one ```python block, nothing omitted).\n\n"
                   f"=== problems found ===\n{report}\n")
        if cmap:
            fix_msg += f"\n=== structure of the code you just wrote (keep calls consistent) ===\n{cmap}\n"
        fix_msg += ("\nDo not introduce new problems. Re-check that every function is called with "
                    "the right arguments and every name is defined before use. Quick pass on the "
                    "usual Linux-GUI traps: any widget a thread/callback touches is stored on self; "
                    "thread results marshalled back to the GUI thread (widget.after / signals, never "
                    "a direct widget call from a worker); the toolkit import wrapped so a missing "
                    "toolkit shows a clear message, not a traceback; no bare except: pass; no "
                    "shell=True on a built command; encoding=\"utf-8\" on text I/O.")
        convo = convo + [
            {"role": "assistant", "content": res["reply"]},
            {"role": "user", "content": fix_msg},
        ]
        nxt = call_model(convo, provider_id, temperature=BUILD_TEMPERATURE)
        if nxt.get("error"):
            res["autotest"] = {"ran": True, "passed": False, "rounds": rounds,
                               "note": "auto-fix call failed: " + nxt["error"]}
            return res
        res = nxt

def review_code(code, provider_id=None):
    """Feature #2 — the 'review my code' button. Runs the independent static analyzer,
    then asks the model for a focused critique (diagnose, don't rewrite). Returns a
    structured report the UI renders. Never modifies the code."""
    if not code or not code.strip():
        return {"error": "There's no code to review yet."}
    # 1. independent static analysis first — concrete, model-blind findings
    analysis = analyze_code(code)
    analyzer_block = ("Automated static analysis: no issues found."
                      if analysis["clean"]
                      else "Automated static analysis (" + analysis["engine"] + ") found:\n- "
                           + "\n- ".join(analysis["issues"]))
    # 2. ask the model to review, given the code + the analyzer's findings
    res = call_model([
        {"role": "system", "content": REVIEW_PROMPT},
        {"role": "user", "content":
            f"Here is the tool to review:\n```python\n{code}\n```\n\n{analyzer_block}"},
    ], provider_id)
    if res.get("error"):
        return res
    parsed = _parse_json_reply(res.get("reply", ""))
    if not parsed:
        # graceful fallback: hand back the analyzer findings even if the model's
        # JSON didn't parse, so the button still does something useful.
        return {"verdict": "Automated checks only (model review unavailable).",
                "issues": [{"severity": "medium", "title": i.split(":")[0] if ":" in i else "issue",
                            "detail": i, "line": None} for i in analysis["issues"]],
                "strengths": [], "engine": analysis["engine"],
                "model": res.get("model")}
    parsed["engine"] = analysis["engine"]
    parsed["model"] = res.get("model")
    # make sure the concrete analyzer findings aren't lost if the model overlooked them
    if not analysis["clean"]:
        parsed.setdefault("analyzer_findings", analysis["issues"])
    return parsed

def _parse_json_reply(reply):
    """Extract a JSON object from a model reply, tolerating fences/prose."""
    reply = re.sub(r"```(?:json)?", "", reply).strip()
    m = re.search(r"\{.*\}", reply, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

def make_intake(request, provider_id=None):
    """Ask the model for a tailored, clickable question set for a tool request."""
    res = call_model([{"role": "system", "content": INTAKE_PROMPT},
                      {"role": "user", "content": request}], provider_id)
    if res.get("error"):
        return res
    parsed = _parse_json_reply(res.get("reply", ""))
    if not parsed or "questions" not in parsed:
        # graceful fallback: no intake, just proceed to build
        return {"intake": None}
    # sanitise
    qs = []
    for q in parsed.get("questions", [])[:6]:
        opts = [str(o) for o in q.get("options", [])][:4]
        if q.get("q") and len(opts) >= 2:
            qs.append({"q": str(q["q"]), "options": opts, "multi": bool(q.get("multi"))})
    return {"intake": {"summary": parsed.get("summary", ""), "questions": qs}}

def structure_followup(reply, convo, provider_id=None):
    """When the model's reply contained NO code, it usually means it asked the user
    something rather than building. Turn those questions into the same tappable
    options the opening intake uses, so the user can answer with a click every time —
    not just on the first message. Returns {"questions": [...]} (possibly empty).
    Cheap-gated: if the reply has no '?' it can't be asking, so we skip the model
    call entirely and return no questions."""
    text = (reply or "").strip()
    if not text or "?" not in text:
        return {"questions": []}
    # a little context keeps the generated options concrete: the user's most recent ask
    last_user = ""
    for m in reversed(convo or []):
        if m.get("role") == "user":
            last_user = (m.get("content") or "")[:600]
            break
    user_blob = (f"For context, the user's last message was:\n{last_user}\n\n" if last_user else "")
    res = call_model([
        {"role": "system", "content": FOLLOWUP_PROMPT},
        {"role": "user", "content":
            user_blob + "The assistant's message to turn into options:\n" + text[:2500]},
    ], provider_id)
    if res.get("error"):
        return {"questions": []}   # never block the build on the optional helper failing
    parsed = _parse_json_reply(res.get("reply", "")) or {}
    qs = []
    for q in parsed.get("questions", [])[:6]:
        opts = [str(o) for o in q.get("options", [])][:4]
        if q.get("q") and len(opts) >= 2:
            qs.append({"q": str(q["q"]), "options": opts, "multi": bool(q.get("multi"))})
    return {"questions": qs}

def make_github(code, details, provider_id=None):
    """Generate README/.gitignore/requirements from the final code + repo details."""
    user = details.get("username", "USER")
    repo = details.get("repo", "tool")
    branch = details.get("branch", "main")
    license_name = details.get("license", "MIT")
    detail_blob = (f"username: {user}\nrepo: {repo}\nbranch: {branch}\n"
                   f"license: {license_name}\nclone over HTTPS only (never ssh).\n"
                   f"raw base: https://raw.githubusercontent.com/{user}/{repo}/{branch}/")
    res = call_model([{"role": "system", "content": GITHUB_PROMPT},
                      {"role": "user", "content":
                       f"Repo details:\n{detail_blob}\n\n=== FINAL CODE ===\n```python\n{code}\n```"}],
                     provider_id)
    if res.get("error"):
        return res
    parsed = _parse_json_reply(res.get("reply", "")) or {}
    return {"github": parsed, "details": details}

# Live GUI processes launched by Run, so we can report status and stop them.
# {pid: {"proc": Popen, "name": str, "path": tmpfile, "started": ts}}
RUNNING = {}
_RUNNING_LOCK = threading.Lock()

def _reap():
    """Drop finished processes and clean up their temp files."""
    with _RUNNING_LOCK:
        for pid in list(RUNNING):
            info = RUNNING[pid]
            if info["proc"].poll() is not None:
                try: os.unlink(info["path"])
                except Exception: pass
                RUNNING.pop(pid, None)

def list_running():
    _reap()
    with _RUNNING_LOCK:
        return {"running": [{"pid": pid, "name": i["name"],
                             "seconds": round(time.time() - i["started"], 1)}
                            for pid, i in RUNNING.items()]}

def stop_running(pid):
    """Terminate a launched GUI (and its children) — cross-platform.
    POSIX: signal the whole process group (we made one with start_new_session=True).
    Windows: taskkill /F /T does the equivalent — terminate the tree."""
    _reap()
    with _RUNNING_LOCK:
        info = RUNNING.get(pid)
    if not info:
        return {"ok": False, "error": "not running (already closed?)"}
    proc = info["proc"]
    try:
        if IS_WIN:
            # taskkill /T = terminate the entire tree, /F = forceful
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10)
            except Exception:
                try: proc.terminate()
                except Exception: pass
                try: proc.kill()
                except Exception: pass
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except Exception:
                proc.terminate()
            try: proc.wait(timeout=3)
            except Exception:
                try: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception: proc.kill()
        return {"ok": True, "pid": pid}
    finally:
        _reap()

def run_code(code, args, confirmed, name="tool"):
    danger = looks_dangerous(code)
    if danger and not confirmed:
        return {"needsConfirm": True, "patterns": danger}

    # parse args the way a shell would (handles quotes/spaces), not naive split
    try:
        argv = shlex.split(args) if args else []
    except ValueError as e:
        return {"stdout": "", "stderr": f"Couldn't parse arguments: {e}", "exit": -1, "seconds": 0}

    tk = detect_toolkit(code)
    interp = run_python(code)

    # unique temp file per run so concurrent/rapid runs can't clobber each other.
    # GUI launches keep their file alive until the window closes (cleaned up by _reap).
    fd, path = tempfile.mkstemp(prefix="thedawg_", suffix=".py")
    with os.fdopen(fd, "w") as f:
        f.write(code)

    # ----- GUI tool: LAUNCH it (don't block on the window) -----------------
    if tk:
        _reap()
        # peek at the first ~1.8s of stderr to catch immediate failures
        # (missing toolkit, missing display, a crash on startup), then leave it running.
        try:
            errf = tempfile.NamedTemporaryFile(prefix="thedawg_err_", suffix=".log", delete=False)
            t0 = time.time()
            # process-group setup so we can cleanly terminate the whole tree later:
            #   POSIX  -> start_new_session=True  (so killpg(getpgid(pid), SIG) works)
            #   Windows -> CREATE_NEW_PROCESS_GROUP (so taskkill /T can find children)
            popen_kw = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=errf,
            )
            if IS_WIN:
                popen_kw["creationflags"] = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                )
            else:
                popen_kw["start_new_session"] = True
            proc = subprocess.Popen([interp, path] + argv, **popen_kw)
        except Exception as e:
            try: os.unlink(path)
            except Exception: pass
            return {"stdout": "", "stderr": f"Could not launch: {e}", "exit": -1, "seconds": 0}

        time.sleep(1.8)
        rc = proc.poll()
        try:
            errf.flush(); errf.close()
            with open(errf.name, "rb") as ef:
                early_err = ef.read().decode("utf-8", errors="replace")
        except Exception:
            early_err = ""
        finally:
            try: os.unlink(errf.name)
            except Exception: pass

        if rc is not None and rc != 0:
            # died on startup — diagnose toolkit / display problems precisely
            hint = ""
            if tk and ("ModuleNotFoundError" in early_err or "ImportError" in early_err
                       or "No module named" in early_err):
                if tk.get("pip"):
                    hint = (f"\n[TheDawg] The {tk['label']} toolkit isn't installed. Install it:\n"
                            f"  pip install {tk['pip']}\n"
                            f"(or click the ⬇ deps button, which does it for you).")
                elif tk["module"] == "tkinter" and IS_LINUX:
                    hint = ("\n[TheDawg] Tkinter ships separately from Python on most distros. "
                            "Install it on this machine:\n"
                            "  " + (install_line("tk") or "install the Tk package for your distro") + "\n"
                            "Elsewhere: sudo apt install python3-tk (Debian/Ubuntu/Kali) · "
                            "sudo dnf install python3-tkinter (Fedora)")
                else:
                    hint = "\n[TheDawg] A required module is missing — see the traceback above."
            elif any(s in early_err for s in ("cannot open display", "no display name",
                      "Unable to init server", "QXcbConnection", "no Qt platform plugin",
                      "could not open display", "couldn't connect to display", "DISPLAY")):
                hint = ("\n[TheDawg] The GUI couldn't open a window — no display is available. "
                        "Launch TheDawg from inside a real desktop session, not over a plain "
                        "SSH shell. The tool itself looks fine.")
            try: os.unlink(path)
            except Exception: pass
            return {"stdout": "", "stderr": (early_err or "the GUI exited immediately") + hint,
                    "exit": rc, "seconds": round(time.time() - t0, 2), "gui": True}

        if rc is not None and rc == 0:
            # opened and closed cleanly within the peek window (or it's a one-shot)
            try: os.unlink(path)
            except Exception: pass
            return {"stdout": "", "stderr": early_err, "exit": 0,
                    "seconds": round(time.time() - t0, 2), "gui": True, "launched": False,
                    "note": "ran and exited cleanly"}

        # still running -> success: the window is open on the user's screen
        with _RUNNING_LOCK:
            RUNNING[proc.pid] = {"proc": proc, "name": name or "tool", "path": path, "started": t0}
        return {"stdout": "", "stderr": early_err, "exit": 0,
                "seconds": round(time.time() - t0, 2), "gui": True, "launched": True,
                "pid": proc.pid,
                "note": f"{tk['label']} window launched (pid {proc.pid}). It's open on your "
                        f"desktop — interact with it there. Use ■ stop to close it."}

    # ----- non-GUI fallback (rare now): capture output as before ----------
    try:
        t0 = time.time()
        try:
            proc = subprocess.run(
                [interp, path] + argv,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=120)
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Killed: exceeded 120s (possible infinite loop, "
                    "or the tool was waiting for input — TheDawg provides none).",
                    "exit": -1, "seconds": round(time.time() - t0, 2)}
        except Exception as e:
            return {"stdout": "", "stderr": f"Could not launch: {e}", "exit": -1, "seconds": 0}

        out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
        errtxt = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        if proc.returncode != 0 and "EOFError" in errtxt and "input(" in code:
            errtxt += ("\n[TheDawg] This tool reads from stdin via input(). The test runner "
                       "doesn't supply interactive input — pass values as command-line args instead.")
        return {"stdout": out, "stderr": errtxt, "exit": proc.returncode,
                "seconds": round(time.time() - t0, 2)}
    finally:
        try: os.unlink(path)
        except Exception: pass

def save_tool(code, name, kind):
    name = re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "tool")).strip("_") or "tool"
    # save under a fixed, predictable home location (never the volatile cwd)
    base = tools_dir()
    tk = detect_toolkit(code)
    if kind == "release":
        d = base / "release" / name
        d.mkdir(parents=True, exist_ok=True)
        pyp = d / (name + ".py")
        pyp.write_text(code + "\n", encoding="utf-8")
        if not IS_WIN:
            try: os.chmod(pyp, 0o755)
            except Exception: pass
        readme = d / "README.md"
        if not readme.exists():
            launch_lin = f"python3 {name}.py"
            pip_note = ""
            if tk and tk.get("pip"):
                pip_note = f"\n\nNeeds: `pip install {tk['pip']}`"
            elif tk and tk.get("apt_hint"):
                pip_note = f"\n\nNeeds: `{tk['apt_hint']}`"
            readme.write_text(
                f"# {name}\n\nA Linux graphical tool built with TheDawg "
                f"(tested on Kali / KDE Plasma).{pip_note}\n\n"
                f"## Usage\n\n```bash\n{launch_lin}\n```\n",
                encoding="utf-8")
        # .desktop entry so a GUI tool appears in the app menu / grid. StartupWMClass
        # helps KDE/GNOME bind the running window to this entry.
        if tk:
            dt = d / (name + ".desktop")
            dt.write_text(
                "[Desktop Entry]\nType=Application\n"
                f"Name={name}\nComment=Built with TheDawg\n"
                f"Exec=python3 {pyp}\nTerminal=false\n"
                f"StartupWMClass={name}\nStartupNotify=true\n"
                "Categories=Utility;Development;\n",
                encoding="utf-8")
        return {"path": str(d), "toolkit": tk["label"] if tk else None}
    else:
        d = base / "forge"
        d.mkdir(parents=True, exist_ok=True)
        pyp = d / (name + ".py")
        pyp.write_text(code + "\n", encoding="utf-8")
        if not IS_WIN:
            try: os.chmod(pyp, 0o755)
            except Exception: pass
        return {"path": str(pyp), "toolkit": tk["label"] if tk else None}

LICENSES = {
    "MIT": ("MIT License\n\nCopyright (c) {year} {holder}\n\nPermission is hereby granted, "
            "free of charge, to any person obtaining a copy of this software and associated "
            "documentation files (the \"Software\"), to deal in the Software without restriction, "
            "including without limitation the rights to use, copy, modify, merge, publish, "
            "distribute, sublicense, and/or sell copies of the Software, and to permit persons "
            "to whom the Software is furnished to do so, subject to the following conditions:\n\n"
            "The above copyright notice and this permission notice shall be included in all "
            "copies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", "
            "WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE "
            "WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. "
            "IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES "
            "OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING "
            "FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE "
            "SOFTWARE.\n"),
}

def _install_sh(user, repo, branch, name, pip_deps=""):
    """POSIX installer (Linux + macOS) — one-line install/update over HTTPS:
       curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/<branch>/install.sh | bash
    Installs the script under ~/.local/share/<repo>, a CLI launcher on PATH, and on
    Linux a .desktop entry. Installs pip deps with --user fallbacks."""
    pip_line = ""
    if pip_deps.strip():
        pip_line = f'''
# install the python deps this tool needs
PIP_PKGS="{pip_deps.strip()}"
echo "installing python deps: $PIP_PKGS"
python3 -m pip install --user $PIP_PKGS --break-system-packages 2>/dev/null \\
  || python3 -m pip install --user $PIP_PKGS \\
  || echo "WARN: pip install failed for: $PIP_PKGS — install manually"
'''
    return f"""#!/usr/bin/env bash
# {repo} installer (Linux / macOS) — one-line install/update:
#   curl -fsSL https://raw.githubusercontent.com/{user}/{repo}/{branch}/install.sh | bash
set -euo pipefail
REPO="{user}/{repo}"; BRANCH="{branch}"
SRC="$HOME/.local/share/{repo}"; BIN="$HOME/.local/bin"; LAUNCH="$BIN/{name}"
APPS="$HOME/.local/share/applications"

command -v python3 >/dev/null 2>&1 || {{ echo "python3 required (>= 3.8)"; exit 1; }}
{pip_line}
mkdir -p "$SRC" "$BIN" "$APPS"
SELF_DIR="$( cd "$( dirname "${{BASH_SOURCE[0]:-$0}}" )" 2>/dev/null && pwd || true )"
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/{name}.py" ]; then
  cp -f "$SELF_DIR/{name}.py" "$SRC/"
  [ -f "$SELF_DIR/requirements.txt" ] && cp -f "$SELF_DIR/requirements.txt" "$SRC/" || true
else
  if command -v git >/dev/null 2>&1; then
    if [ -d "$SRC/.git" ]; then git -C "$SRC" pull --ff-only --quiet || true
    else rm -rf "$SRC"; git clone --depth 1 -b "$BRANCH" "https://github.com/$REPO.git" "$SRC" --quiet; fi
  else
    TARBALL="https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH"
    if command -v curl >/dev/null 2>&1; then curl -fsSL "$TARBALL" | tar xz -C "$SRC" --strip-components=1
    elif command -v wget >/dev/null 2>&1; then wget -qO- "$TARBALL" | tar xz -C "$SRC" --strip-components=1
    else echo "need git, curl, or wget"; exit 1; fi
  fi
fi

# CLI launcher
cat > "$LAUNCH" <<EOF
#!/usr/bin/env bash
exec python3 "$SRC/{name}.py" "\\$@"
EOF
chmod +x "$LAUNCH"

# desktop entry (Linux only — harmless on macOS)
if [ "$(uname -s)" = "Linux" ]; then
  cat > "$APPS/{name}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name={name}
Comment={repo} — built with TheDawg
Exec=python3 $SRC/{name}.py
Terminal=false
Categories=Utility;Development;
EOF
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

case ":$PATH:" in *":$BIN:"*) ;; *)
  RC="$HOME/.bashrc"; [ -n "${{ZSH_VERSION:-}}" ] && RC="$HOME/.zshrc"
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC"
  echo "added $BIN to PATH in $RC — run: source $RC" ;;
esac
echo "installed {name}. launch from your app grid (Linux), or run: {name}"
"""

def write_github_repo(code, name, gh, details):
    """Write a complete polished repo into ~/thedawg-tools/github/<repo>/.
    Includes install.sh (Linux, curl|bash) so a release installs cleanly on Kali
    (KDE Plasma) and other Linux desktops, under Wayland or X11."""
    name = re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "tool")).strip("_") or "tool"
    user = details.get("username", "USER")
    repo = re.sub(r"[^A-Za-z0-9_.\-]", "-", details.get("repo", name)) or name
    branch = details.get("branch", "main")
    license_name = details.get("license", "MIT")
    holder = details.get("holder", user)

    d = tools_dir() / "github" / repo
    d.mkdir(parents=True, exist_ok=True)
    d = str(d)

    # main script
    pyp = os.path.join(d, name + ".py")
    with open(pyp, "w", encoding="utf-8") as f:
        f.write(code + "\n")
    if not IS_WIN:
        try: os.chmod(pyp, 0o755)
        except Exception: pass

    # README (AI-generated, with fallback)
    fallback_readme = (
        f"# {repo}\n\n{gh.get('description', 'A Linux graphical Python tool built with TheDawg.')}\n\n"
        f"A native **Linux desktop** GUI tool — tested on Kali (KDE Plasma).\n\n"
        f"## Install\n\n"
        f"```bash\ncurl -fsSL https://raw.githubusercontent.com/{user}/{repo}/{branch}/install.sh | bash\n```\n\n"
        f"## Usage\n\nLaunch from your app grid / launcher, or run `{name}` in a terminal.\n"
    )
    readme = gh.get("readme") or fallback_readme
    with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
        f.write(readme)

    # .gitignore
    with open(os.path.join(d, ".gitignore"), "w", encoding="utf-8") as f:
        f.write(gh.get("gitignore") or
                "__pycache__/\n*.py[cod]\n.venv/\nvenv/\n.env\n*.key\n.DS_Store\n"
                "build/\ndist/\n*.spec\n")

    # requirements (only if non-empty)
    reqs = (gh.get("requirements") or "").strip()
    if reqs:
        with open(os.path.join(d, "requirements.txt"), "w", encoding="utf-8") as f:
            f.write(reqs + "\n")

    # derive pip deps line for the installers (joins requirements.txt-style lines into "pkg1 pkg2")
    pip_deps = " ".join(line.split("#", 1)[0].strip()
                        for line in reqs.splitlines() if line.strip() and not line.startswith("#"))

    # install.sh (Linux)
    ish = os.path.join(d, "install.sh")
    with open(ish, "w", encoding="utf-8", newline="\n") as f:
        f.write(_install_sh(user, repo, branch, name, pip_deps))
    if not IS_WIN:
        try: os.chmod(ish, 0o755)
        except Exception: pass

    # .desktop entry — for Linux users to drop into ~/.local/share/applications.
    # StartupWMClass helps KDE/GNOME bind the running window to this entry's icon.
    desktop = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={name}\n"
        f"Comment={gh.get('description', repo + ' — built with TheDawg')}\n"
        f"Exec=python3 %h/.local/share/{repo}/{name}.py\n"
        "Terminal=false\n"
        f"StartupWMClass={name}\n"
        "StartupNotify=true\n"
        "Categories=Utility;Development;\n"
    )
    with open(os.path.join(d, name + ".desktop"), "w", encoding="utf-8") as f:
        f.write(desktop)

    # LICENSE
    lic = LICENSES.get(license_name)
    if lic:
        with open(os.path.join(d, "LICENSE"), "w", encoding="utf-8") as f:
            f.write(lic.format(year=time.strftime("%Y"), holder=holder))

    # the exact push commands, HTTPS only
    push = [
        "cd " + repo,
        "git init",
        "git add .",
        f'git commit -m "{repo} — initial release"',
        f"git branch -M {branch}",
        f"git remote add origin https://github.com/{user}/{repo}.git",
        f"git push -u origin {branch}",
    ]
    return {
        "path": d,
        "files": sorted(os.listdir(d)),
        "push": push,
        "install_line_posix": f"curl -fsSL https://raw.githubusercontent.com/{user}/{repo}/{branch}/install.sh | bash",
    }

# --------------------------------------------------------------------------
# PYINSTALLER  -- pack a tool into a standalone Linux binary
# --------------------------------------------------------------------------
# TheDawg builds a single-file binary for Linux via PyInstaller in its managed
# venv. (No cross-compilation: PyInstaller bakes the host Python + libs into the
# output, so a binary built here runs on Linux only — which is the target.)
def build_executable(code, name, console=False):
    """Run PyInstaller in TheDawg's managed venv to produce a single-file Linux
    binary. Returns the path to the artefact + a tail of the build log."""
    name = re.sub(r"[^A-Za-z0-9_\-]", "_", (name or "tool")).strip("_") or "tool"
    if not code or not code.strip():
        return {"ok": False, "log": "no code to build"}

    # 1) ensure the venv exists and PyInstaller is installed in it
    venv_py = _venv_python()
    if not venv_py:
        # build the venv lazily so the user doesn't pay the cost until they actually build
        try:
            import venv
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(VENV_DIR)
            venv_py = _venv_python() or sys.executable
        except Exception as e:
            return {"ok": False, "log": f"venv creation failed: {e}"}

    # also install whatever the TOOL imports (toolkit + pip deps) so PyInstaller can
    # actually find them when it sniffs the script
    deps = detect_deps(code)
    pip_to_install = ["pyinstaller"] + [p for p in deps["pip"] if p]
    try:
        proc = subprocess.run([venv_py, "-m", "pip", "install", "--upgrade", *pip_to_install],
                              capture_output=True, text=True, timeout=900,
                              encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            return {"ok": False, "log": "pip install failed:\n" + (proc.stderr or proc.stdout)[-2000:]}
    except Exception as e:
        return {"ok": False, "log": f"pip install error: {e}"}

    # 2) lay out a work dir under thedawg-tools/dist/<name>/
    workdir = tools_dir() / "dist" / name
    workdir.mkdir(parents=True, exist_ok=True)
    py_file = workdir / (name + ".py")
    py_file.write_text(code, encoding="utf-8")

    dist_dir  = workdir / "out"
    build_dir = workdir / "build"
    spec_dir  = workdir / "spec"
    for p in (dist_dir, build_dir, spec_dir):
        p.mkdir(exist_ok=True)

    # 3) build args: --onefile bakes everything into one binary, --windowed drops the
    #    controlling console for GUI tools, --clean wipes PyInstaller's cache so
    #    re-builds always reflect the latest code
    args = [venv_py, "-m", "PyInstaller", "--onefile", "--clean", "--noconfirm",
            "--name", name,
            "--distpath", str(dist_dir),
            "--workpath", str(build_dir),
            "--specpath", str(spec_dir)]
    if not console:
        args.append("--windowed")
    # bundle the Dawg icon if present (PyInstaller takes a PNG on Linux)
    icon_png = Path(HERE) / "assets" / "icon.png"
    if icon_png.exists():
        args += ["--icon", str(icon_png)]
    args.append(str(py_file))

    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=1200,
                              encoding="utf-8", errors="replace")
        log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return {"ok": False, "log": "PyInstaller timed out after 20 minutes."}
    except Exception as e:
        return {"ok": False, "log": f"PyInstaller crashed: {e}"}

    # 4) find the artefact
    out_name, target = name, "Linux binary"
    out_path = dist_dir / out_name
    if proc.returncode == 0 and out_path.exists():
        size_mb = round(out_path.stat().st_size / (1024 * 1024), 1)
        return {"ok": True, "path": str(out_path), "target": target,
                "size_mb": size_mb, "log": log[-2000:]}
    return {"ok": False, "log": "PyInstaller didn't produce a binary.\n\n" + log[-2500:]}


# --------------------------------------------------------------------------
# SESSION LOG  -- every run is appended; one button hands it all to the model
# --------------------------------------------------------------------------
SESSION_LOG = []   # list of dicts: {ts, kind, name, args, exit, seconds, stdout, stderr}

def log_run(name, args, result):
    SESSION_LOG.append({
        "ts": time.strftime("%H:%M:%S"),
        "name": name, "args": args,
        "exit": result.get("exit"), "seconds": result.get("seconds"),
        "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""),
    })
    # keep it bounded so we never blow the context window
    if len(SESSION_LOG) > 40:
        del SESSION_LOG[0:len(SESSION_LOG) - 40]

def render_log(full=True):
    """Render the session log as a single text blob (also what gets saved to file)."""
    lines = [f"TheDawg session log — {len(SESSION_LOG)} run(s)", "=" * 50]
    for i, e in enumerate(SESSION_LOG, 1):
        lines.append(f"\n[run {i}] {e['ts']}  {e['name']}.py {e['args']}".rstrip())
        lines.append(f"exit {e['exit']} · {e['seconds']}s")
        if e["stdout"]:
            out = e["stdout"] if full else e["stdout"][-1500:]
            lines.append("--- stdout ---\n" + out.rstrip())
        if e["stderr"]:
            lines.append("--- stderr ---\n" + e["stderr"].rstrip())
    return "\n".join(lines)

def fix_from_log(code, messages, provider_id=None):
    """Send the current code + the run log + the latest runtime probe (what the
    window actually did and how it looked) to the model for a fix."""
    probe_report = LAST_PROBE.get("report") if LAST_PROBE.get("ran") else ""
    if not SESSION_LOG and not probe_report:
        return {"error": "Nothing observed yet — press \u25b6 launch or \U0001f50e self-test first, "
                         "then I'll have something to diagnose."}
    log_blob = render_log(full=False) if SESSION_LOG else "(no manual runs logged)"
    convo = [m for m in messages if m.get("role") != "system"]
    extra = ""
    if probe_report:
        extra = ("\n\n=== RUNTIME PROBE (TheDawg opened the window and looked at it) ===\n"
                 + probe_report)
    convo = [{"role": "system", "content": SYSTEM_PROMPT}] + convo + [{
        "role": "user",
        "content": (
            "Here is the current tool plus everything TheDawg observed when it ran: the run "
            "log, and (if present) a runtime probe that actually opened the window, watched "
            "whether it stayed up, screenshotted it, and poked it. Diagnose every problem you "
            "can see and return the FULL corrected script. Briefly list what you fixed.\n\n"
            f"=== CURRENT CODE ===\n```python\n{code}\n```\n\n"
            f"=== RUN LOG ===\n{log_blob}" + extra
        )
    }]
    return chat_with_autotest(convo, provider_id)

def polish_round(code, messages, provider_id=None):
    """One iteration of the auto-polish loop. Before asking the model to improve the
    tool, TheDawg actually TESTS it: a static smoke test AND a runtime probe that
    opens the window, screenshots it, checks it isn't blank, and pokes it to surface
    click-crashes. Any real failure observed is handed to the model as the #1 thing
    to fix — so polish hunts genuine bugs instead of just gold-plating."""
    # 1) static smoke
    passed, report, _ = smoke_test(code)
    # 2) runtime probe (opens the real window headlessly and looks at it)
    probe = probe_run(code, name="tool")

    problems = []
    if probe.get("ran"):
        if probe.get("crashed_on_interact"):
            problems.append("CRITICAL: the window opens but CRASHES on interaction "
                            "(a keypress/click kills it). Fix the failing callback — "
                            "traceback is in the probe report.")
        elif probe.get("alive") is False:
            problems.append("CRITICAL: the tool CRASHES on startup — the window does not "
                            "stay open. Fix the startup error (traceback in the probe report).")
        a = probe.get("analysis") or {}
        if probe.get("alive") and a.get("ok") and a.get("blank"):
            problems.append("CRITICAL: the window opens but renders BLANK (nothing is drawn). "
                            "Make the UI actually populate — pack/grid/add the widgets, run the "
                            "paint routine, set a real geometry.")
    if not passed:
        problems.append("It fails a static check:\n" + report)

    log_blob = render_log(full=False) if SESSION_LOG else "(no manual runs yet)"
    probe_section = ""
    if probe.get("ran"):
        probe_section = ("\n\n=== RUNTIME PROBE (TheDawg opened the window and looked) ===\n"
                         + probe.get("report", ""))

    if problems:
        directive = (
            "TheDawg tested this tool and found real problems. FIX THESE FIRST, in order, "
            "before anything else:\n\n- " + "\n- ".join(problems) +
            "\n\nReturn the FULL corrected script. After fixing the above, you may make one "
            "small additional robustness improvement, but the priority is making the tool "
            "actually run and render correctly. One line on what you changed."
        )
    else:
        directive = (
            "TheDawg tested this tool: it passes the static smoke test and the runtime probe "
            "(window opens, stays up, renders real content, survives interaction). Since it's "
            "healthy, improve it by ONE meaningful increment — harden an edge case, improve "
            "output clarity, or add the single most valuable missing feature — but keep it ONE "
            "self-contained script and don't over-engineer. Return the FULL improved script and "
            "one line on what you changed."
        )

    convo = [{"role": "system", "content": SYSTEM_PROMPT}, {
        "role": "user",
        "content": (directive +
                    f"\n\n=== CODE ===\n```python\n{code}\n```\n\n"
                    f"=== RECENT RUNS ===\n{log_blob}" + probe_section)
    }]
    return chat_with_autotest(convo, provider_id)

# ==========================================================================
# http
# ==========================================================================
# Static files (the UI, icons, sounds) are read once and held in memory, keyed by
# (path, mtime, size). The UI is ~90 KB of HTML; re-reading and re-sending it
# uncompressed on every load was the single slowest thing about opening TheDawg.
_STATIC_CACHE = {}
_STATIC_LOCK = threading.Lock()
_GZIP_MIN = 1024          # below this, compression costs more than it saves


def _read_static(path):
    """Cached file read. Returns (bytes, etag) or (None, None) if missing."""
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    key = (path, st.st_mtime_ns, st.st_size)
    with _STATIC_LOCK:
        hit = _STATIC_CACHE.get(path)
        if hit and hit[0] == key:
            return hit[1], hit[2]
    try:
        with open(path, "rb") as f:
            blob = f.read()
    except OSError:
        return None, None
    etag = '"%x-%x"' % (st.st_mtime_ns, st.st_size)
    gz = None
    if len(blob) >= _GZIP_MIN:
        try:
            import gzip as _gz
            cand = _gz.compress(blob, 6)
            if len(cand) < len(blob) * 0.92:
                gz = cand
        except Exception:
            gz = None
    with _STATIC_LOCK:
        _STATIC_CACHE[path] = (key, blob, etag, gz)
    return blob, etag


def _static_gzip(path):
    with _STATIC_LOCK:
        hit = _STATIC_CACHE.get(path)
    return hit[3] if hit else None


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.1 keeps the connection alive between requests. The UI fires a burst of
    # small API calls on load; on HTTP/1.0 each one paid a fresh TCP handshake.
    protocol_version = "HTTP/1.1"
    # every response sets Content-Length, so keep-alive is safe
    server_version = "TheDawg"
    sys_version = ""

    def log_message(self, *a):  # quiet
        pass

    def _accepts_gzip(self):
        return "gzip" in (self.headers.get("Accept-Encoding") or "")

    def _send(self, code, body, ctype="application/json", extra=None, gz=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, separators=(",", ":")).encode()
        elif isinstance(body, str):
            body = body.encode()
        encoding = None
        if gz is not None and self._accepts_gzip():
            body, encoding = gz, "gzip"
        elif len(body) >= _GZIP_MIN and self._accepts_gzip() and (
                ctype.startswith("text/") or "json" in ctype or "svg" in ctype):
            try:
                import gzip as _gz
                cand = _gz.compress(body, 5)
                if len(cand) < len(body) * 0.92:
                    body, encoding = cand, "gzip"
            except Exception:
                pass
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _file(self, path, ctype, cache="no-cache"):
        blob, etag = _read_static(path)
        if blob is None:
            self._send(404, {"error": "not found"})
            return
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(200, blob, ctype,
                   extra={"ETag": etag, "Cache-Control": cache},
                   gz=_static_gzip(path))

    def do_GET(self):
        # Strip the query string before routing. Every branch below compares
        # against a bare path, so a single "?native=1" was enough to 404 the
        # whole app — which is exactly what the native shell appends.
        self.path = self.path.split("?", 1)[0] or "/"
        if self.path in ("/", "/index.html"):
            self._file(os.path.join(HERE, "ui", "index.html"), "text/html; charset=utf-8")
        elif self.path.startswith("/assets/"):
            name = os.path.basename(self.path)
            ext = name.rsplit(".", 1)[-1].lower()
            ctype = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp",
                     "jpg": "image/jpeg", "jpeg": "image/jpeg", "ico": "image/x-icon",
                     "woff2": "font/woff2"}.get(ext, "application/octet-stream")
            self._file(os.path.join(HERE, "assets", name), ctype, cache="public, max-age=86400")
        elif self.path.startswith("/sounds/"):
            # serve any audio file the user dropped in the sounds/ directory.
            # supports mp3, wav, ogg, m4a, flac — whatever the browser can play.
            name = os.path.basename(self.path)
            # only serve plain filenames — no path traversal
            if "/" in name or "\\" in name or name.startswith("."):
                self._send(404, {"error": "not found"}); return
            ext = name.rsplit(".", 1)[-1].lower()
            ctype = {
                "mp3":  "audio/mpeg",
                "wav":  "audio/wav",
                "ogg":  "audio/ogg",
                "oga":  "audio/ogg",
                "m4a":  "audio/mp4",
                "flac": "audio/flac",
                "aac":  "audio/aac",
            }.get(ext, "application/octet-stream")
            full = os.path.join(HERE, "sounds", name)
            if not os.path.isfile(full):
                self._send(404, {"error": "no such sound"}); return
            self._file(full, ctype, cache="public, max-age=86400")
        elif self.path.startswith("/vendor/"):
            name = os.path.basename(self.path)
            if "/" in name or "\\" in name or name.startswith("."):
                self._send(404, {"error": "not found"}); return
            ext = name.rsplit(".", 1)[-1].lower()
            ctype = {"js": "text/javascript; charset=utf-8", "css": "text/css; charset=utf-8",
                     "woff2": "font/woff2", "woff": "font/woff",
                     "ttf": "font/ttf", "svg": "image/svg+xml"}.get(ext, "application/octet-stream")
            self._file(os.path.join(HERE, "ui", "vendor", name), ctype,
                       cache="public, max-age=604800, immutable")
        elif self.path == "/api/sounds":
            # tell the UI which trigger files actually exist, so it knows what to play.
            # The UI looks for these filenames in HERE/sounds/:
            #   startup.{mp3|wav|ogg|m4a}      — played when TheDawg opens
            #   done.{mp3|wav|ogg|m4a}         — played when the model finishes a tool
            #   build.{mp3|wav|ogg|m4a}        — played when PyInstaller succeeds
            # User can drop any one of those extensions; we pick the first that exists.
            sdir = os.path.join(HERE, "sounds")
            os.makedirs(sdir, exist_ok=True)
            mapping = {}
            for trigger in ("startup", "done", "build"):
                for ext in ("mp3", "wav", "ogg", "m4a", "flac"):
                    cand = f"{trigger}.{ext}"
                    if os.path.isfile(os.path.join(sdir, cand)):
                        mapping[trigger] = "/sounds/" + cand
                        break
            self._send(200, {"sounds": mapping, "dir": sdir})
        elif self.path == "/api/status":
            provs = []
            for pid, p in PROVIDERS.items():
                chain = provider_model_chain(pid)   # live if cached, else fallback
                provs.append({"id": pid, "label": p["label"],
                              "hasKey": bool(STATE["keys"].get(pid)),
                              "models": chain,
                              "chosen": STATE["models"].get(pid) or (chain[0] if chain else "?"),
                              "topModel": chain[0] if chain else "?",
                              "live": pid in _MODEL_CACHE})
            cur_chain = provider_model_chain(STATE["provider"])
            chosen_cur = STATE["models"].get(STATE["provider"]) or (cur_chain[0] if cur_chain else "?")
            self._send(200, {
                "providers": provs,
                "provider": STATE["provider"],
                "model": chosen_cur,
                "hasKey": bool(STATE["keys"].get(STATE["provider"])),
                "autotest": AUTOTEST_MAX_ROUNDS,
                "version": __version__,
                "desktop": detect_desktop_env(),
                "distro": DISTRO,
                "native": NATIVE_SHELL.get("active", False),
                "shell": NATIVE_SHELL.get("kind", ""),
            })
        elif self.path == "/api/log":
            self._send(200, {"log": render_log(full=True), "runs": len(SESSION_LOG)})
        elif self.path == "/api/library":
            self._send(200, library_list())
        elif self.path == "/api/running":
            self._send(200, list_running())
        elif self.path == "/api/sessions":
            self._send(200, session_list())
        elif self.path == "/api/log.txt":
            blob = render_log(full=True).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=thedawg-session.log")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
        elif self.path == "/api/shot.png":
            # latest runtime-probe screenshot (UI cache-busts with ?t=...)
            if os.path.exists(SHOT_PATH):
                self._file(SHOT_PATH, "image/png", cache="no-store")
            else:
                self._send(404, {"error": "no screenshot yet"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        self.path = self.path.split("?", 1)[0] or "/"
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:
            return self._send(400, {"error": "bad json"})

        if self.path == "/api/key":
            pid = data.get("provider") or STATE["provider"]
            if pid not in PROVIDERS:
                return self._send(200, {"error": "unknown provider"})
            STATE["keys"][pid] = (data.get("key") or "").strip()
            saved = persist_state() if STATE["keys"][pid] else False
            # a new key means we can now ask the provider what it actually offers
            fetched = None
            if STATE["keys"][pid]:
                _MODEL_CACHE.pop(pid, None)
                _HOST_OK.pop(pid, None)
                fetched = fetch_models(pid, force=True)
            self._send(200, {"hasKey": bool(STATE["keys"][pid]), "saved": saved,
                             "models": (fetched or {}).get("models"),
                             "modelSource": (fetched or {}).get("source"),
                             "modelError": (fetched or {}).get("error")})
        elif self.path == "/api/provider":
            pid = data.get("provider")
            if pid not in PROVIDERS:
                return self._send(200, {"error": "unknown provider"})
            STATE["provider"] = pid
            persist_state()
            chain = provider_model_chain(pid)
            self._send(200, {"provider": pid, "hasKey": bool(STATE["keys"].get(pid)),
                             "model": STATE["models"].get(pid) or (chain[0] if chain else "?")})
        elif self.path == "/api/models/refresh":
            pid = data.get("provider") or STATE["provider"]
            if pid not in PROVIDERS:
                return self._send(200, {"error": "unknown provider"})
            self._send(200, {"provider": pid, **fetch_models(pid, force=True)})
        elif self.path == "/api/model":
            pid = data.get("provider") or STATE["provider"]
            model = data.get("model")
            if pid not in PROVIDERS:
                return self._send(200, {"error": "unknown provider"})
            # accept any model from the live catalog OR the static fallback
            valid = set(provider_model_chain(pid)) | set(PROVIDERS[pid]["models"])
            if model and model in valid:
                STATE["models"][pid] = model
                persist_state()
                self._send(200, {"provider": pid, "model": model})
            else:
                self._send(200, {"error": "unknown model for this provider"})
        elif self.path == "/api/chat":
            # The methodology prompt is authoritative and lives here, server-side.
            convo = [m for m in data.get("messages", []) if m.get("role") != "system"]
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + convo
            provider = data.get("provider")  # optional per-request override
            self._send(200, chat_with_autotest(messages, provider))
        elif self.path == "/api/run":
            result = run_code(data.get("code", ""), data.get("args", ""),
                              bool(data.get("confirm")), data.get("name", "tool"))
            # log only actual runs (not the confirm-gate response)
            if "needsConfirm" not in result:
                log_run(data.get("name", "tool"), data.get("args", ""), result)
            self._send(200, result)
        elif self.path == "/api/stop":
            self._send(200, stop_running(int(data.get("pid", 0) or 0)))
        elif self.path == "/api/probe":
            # TheDawg self-test: open the window headlessly, screenshot it, poke it,
            # and report what it saw. Also logged so "send log to AI" has context.
            p = probe_run(data.get("code", ""), data.get("name", "tool"))
            if p.get("ran"):
                if p.get("crashed_on_interact"):
                    verdict = "self-test: window CRASHES on interaction"
                elif p.get("alive") is False:
                    verdict = "self-test: tool CRASHES on startup"
                elif (p.get("analysis") or {}).get("blank"):
                    verdict = "self-test: window opens but renders BLANK"
                else:
                    verdict = "self-test: window opens and renders content"
            else:
                verdict = "self-test: " + (p.get("kind") or "could not run")
            log_run(data.get("name", "tool"), "(self-test)",
                    {"ok": p.get("ok", False), "stdout": verdict,
                     "stderr": p.get("stderr", "")})
            self._send(200, {
                "ran": p.get("ran", False), "shot": p.get("shot", False),
                "alive": p.get("alive"), "rc": p.get("rc"), "secs": p.get("secs"),
                "headless": p.get("headless"), "toolkit": p.get("toolkit"),
                "analysis": p.get("analysis"), "interacted": p.get("interacted"),
                "crashed_on_interact": p.get("crashed_on_interact"),
                "ok": p.get("ok", False), "kind": p.get("kind"),
                "report": p.get("report", ""),
            })
        elif self.path == "/api/fixlog":
            convo = data.get("messages", [])
            self._send(200, fix_from_log(data.get("code", ""), convo, data.get("provider")))
        elif self.path == "/api/review":
            self._send(200, review_code(data.get("code", ""), data.get("provider")))
        elif self.path == "/api/intake":
            self._send(200, make_intake(data.get("request", ""), data.get("provider")))
        elif self.path == "/api/github":
            self._send(200, make_github(data.get("code", ""), data.get("details", {}),
                                        data.get("provider")))
        elif self.path == "/api/github/write":
            try:
                self._send(200, write_github_repo(data.get("code", ""), data.get("name", "tool"),
                                                  data.get("github", {}), data.get("details", {})))
            except Exception as e:
                self._send(200, {"error": str(e)})
        elif self.path == "/api/log.clear":
            SESSION_LOG.clear()
            self._send(200, {"runs": 0})
        elif self.path == "/api/library/save":
            self._send(200, library_save(data.get("name", "tool"), data.get("code", ""),
                                         data.get("messages", []),
                                         data.get("version", "testing"),
                                         data.get("args", ""), data.get("sessionId"),
                                         data.get("ver", "1.0.0"),
                                         bool(data.get("named", False)),
                                         data.get("title", "")))
        elif self.path == "/api/library/load":
            self._send(200, library_load(data.get("id", "")))
        elif self.path == "/api/library/delete":
            self._send(200, library_delete(data.get("id", "")))
        elif self.path == "/api/session/save":
            self._send(200, session_save(data.get("id"), data.get("name", "untitled"),
                                         data.get("code", ""), data.get("messages", []),
                                         data.get("version", "testing"), data.get("args", ""),
                                         data.get("ver", "1.0.0"),
                                         bool(data.get("named", False)),
                                         data.get("title", "")))
        elif self.path == "/api/session/load":
            self._send(200, session_load(data.get("id", "")))
        elif self.path == "/api/session/delete":
            self._send(200, session_delete(data.get("id", "")))
        elif self.path == "/api/deps":
            self._send(200, detect_deps(data.get("code", "")))
        elif self.path == "/api/deps/install":
            self._send(200, install_deps(data.get("pip", []) or data.get("deps", [])))
        elif self.path == "/api/build":
            try:
                self._send(200, build_executable(
                    data.get("code", ""),
                    data.get("name", "tool"),
                    bool(data.get("console", False))))
            except Exception as e:
                self._send(200, {"ok": False, "log": f"build crashed: {e}"})
        elif self.path == "/api/platform":
            self._send(200, {"os": platform.system(), "python": platform.python_version(),
                             "is_win": IS_WIN, "is_mac": IS_MAC, "is_linux": IS_LINUX,
                             "desktop": detect_desktop_env(), "distro": DISTRO,
                             "cpus": cpu_threads()})
        elif self.path == "/api/polish":
            convo = data.get("messages", [])
            self._send(200, polish_round(data.get("code", ""), convo, data.get("provider")))
        elif self.path == "/api/save":
            try:
                self._send(200, save_tool(data.get("code", ""), data.get("name", "tool"),
                                          data.get("kind", "testing")))
            except Exception as e:
                self._send(200, {"error": str(e)})
        elif self.path == "/api/quit":
            self._send(200, {"ok": True})
            # shut the server down shortly after responding
            threading.Thread(target=lambda: (time.sleep(0.3), os._exit(0)), daemon=True).start()
        else:
            self._send(404, {"error": "not found"})

def free_port(host, start):
    for p in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.15)
            if s.connect_ex((host, p)) != 0:
                return p
    return start


# ==========================================================================
# FRONT DOOR  -- how TheDawg actually appears on your desktop
# ==========================================================================
# Preference order:
#   1. NATIVE: a GTK4 + libadwaita window owning a WebKitGTK view (shell.py).
#      One process, real titlebar, real app_id, no browser profile on disk.
#   2. Chromium `--app=` window, if the native bindings aren't installed.
#   3. A plain browser tab, as a last resort.
NATIVE_SHELL = {"active": False, "kind": "", "reason": ""}


def launch_browser_window(url):
    """Fallback front door: a Chromium-family app window, else an ordinary tab."""
    candidates = []
    if IS_WIN:
        candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        ]
    elif IS_MAC:
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
    else:
        for binname in ("brave-browser", "brave", "chromium", "chromium-browser",
                        "google-chrome-stable", "google-chrome", "microsoft-edge", "vivaldi"):
            p = shutil.which(binname)
            if p:
                candidates.append(p)

    app_data = str(app_data_dir() / "window")
    for path in candidates:
        if not path:
            continue
        resolved = path if os.path.isabs(path) else shutil.which(path)
        if not resolved or not os.path.exists(resolved):
            continue
        try:
            argv = [resolved, f"--app={url}",
                    f"--user-data-dir={app_data}",
                    "--no-first-run", "--no-default-browser-check",
                    "--autoplay-policy=no-user-gesture-required",
                    "--window-size=1360,900"]
            if IS_LINUX:
                # matches StartupWMClass in the .desktop entry, so the task
                # switcher shows the Dawg and not a generic browser icon
                argv.insert(1, "--class=thedawg")
                if (detect_desktop_env().get("session") == "wayland"
                        and os.environ.get("THEDAWG_X11") != "1"):
                    argv.insert(1, "--ozone-platform-hint=auto")
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.basename(resolved)
        except Exception:
            continue
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return None


# kept as an alias so older launchers / scripts calling this still work
launch_app_window = launch_browser_window


# ==========================================================================
# DOCTOR  -- one command that tells you exactly what to install, in YOUR
# distro's package names. `thedawg --doctor`
# ==========================================================================
def doctor():
    de = detect_desktop_env()
    print(f"\n  TheDawg v{__version__}  ·  environment check")
    print(f"  {'-' * 62}")
    print(f"  distro      : {DISTRO['pretty']}  ({DISTRO['family']} family)")
    print(f"  desktop     : {de['raw'] or de['de']} on {de['session']}")
    print(f"  python      : {platform.python_version()}  ({sys.executable})")
    print(f"  cpu threads : {cpu_threads()}")
    if is_cachy():
        print("  cachyos     : yes — using the optimised toolchain paths")
    print()

    rows = []

    # native shell
    try:
        sys.path.insert(0, HERE)
        import shell as _shell
        ok = _shell.available()
        rows.append(("native app window (GTK4 + WebKitGTK)", ok,
                     "" if ok else _shell.missing_reason(),
                     install_line("pygobject", "gtk4", "libadwaita", "webkitgtk6")))
    except Exception as e:
        rows.append(("native app window (GTK4 + WebKitGTK)", False, str(e),
                     install_line("pygobject", "gtk4", "libadwaita", "webkitgtk6")))

    # self-test stack
    rows.append(("self-test: headless display (Xvfb)", bool(shutil.which("Xvfb")), "",
                 install_line("xvfb")))
    rows.append(("self-test: synthetic input (xdotool)", bool(shutil.which("xdotool")), "",
                 install_line("xdotool")))
    rows.append(("self-test: screen capture", bool(shutil.which("import") or shutil.which("magick")
                                                   or shutil.which("maim") or shutil.which("scrot")), "",
                 install_line("imagemagick")))
    try:
        import PIL  # noqa: F401
        pil_ok = True
    except Exception:
        pil_ok = False
    rows.append(("self-test: screenshot analysis (Pillow)", pil_ok, "", install_line("pillow")))

    # build + lint
    rows.append(("single-file builds (PyInstaller)", bool(shutil.which("pyinstaller")), "",
                 install_line("pyinstaller") + "   # or: pip install pyinstaller"))
    rows.append(("fast lint/autofix (ruff)", bool(_ruff_path()), "",
                 install_line("ruff") + "   # or: pip install ruff"))
    rows.append(("git (for the GitHub release flow)", bool(shutil.which("git")), "",
                 (DISTRO["install"] + " git") if DISTRO["install"] else "install git"))

    missing = []
    for label, ok, why, fix in rows:
        mark = "\u2713" if ok else "\u2717"
        note = "" if ok else (f"  — {why}" if why else "")
        print(f"   {mark}  {label}{note}")
        if not ok and fix:
            missing.append(fix)

    print()
    if missing:
        print("  to fix everything above:\n")
        # collapse the ones that share a package manager into a single line
        pm = DISTRO["install"]
        pkgs, extra = [], []
        for line in missing:
            if pm and line.startswith(pm):
                pkgs.extend(line[len(pm):].split("#")[0].split())
                if "#" in line:
                    extra.append("  " + line.split("#", 1)[1].strip())
            else:
                extra.append("  " + line)
        seen, uniq = set(), []
        for p in pkgs:
            if p not in seen:
                seen.add(p); uniq.append(p)
        if uniq:
            print(f"    {pm} {' '.join(uniq)}")
        for e in extra:
            print(f"  {e}")
    else:
        print("  everything TheDawg can use is installed. nice.")

    keys = [PROVIDERS[p]["label"] for p in PROVIDERS if STATE["keys"].get(p)]
    print(f"\n  api keys    : {', '.join(keys) if keys else 'none yet — add one in Settings'}")
    print()


def main():
    argv = sys.argv[1:]
    if "--doctor" in argv or "--check" in argv:
        doctor(); return
    if "--version" in argv or "-V" in argv:
        print(f"TheDawg {__version__}"); return
    if "--help" in argv or "-h" in argv:
        print(f"""
  TheDawg {__version__} — AI Python toolsmith for the Linux desktop

    thedawg                 launch (native window if available)
    thedawg --browser       force the browser front door instead
    thedawg --doctor        check this machine and print exact install commands
    thedawg --safe-gfx      disable the WebKit dmabuf renderer (blank/black window fix)
    thedawg --dev           open with developer tools
    thedawg --port N        start looking for a free port at N
    thedawg --version
""")
        return

    dev = "--dev" in argv
    force_browser = "--browser" in argv or "--no-native" in argv
    if "--safe-gfx" in argv or os.environ.get("THEDAWG_SAFE_GFX") == "1":
        # some Mesa/NVIDIA combinations render a WebKitGTK view black until the
        # dmabuf path is turned off. Must be set before WebKit spawns.
        os.environ["WEBKIT_DISABLE_DMABUF_RENDERER"] = "1"
        os.environ["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"

    start_port = PORT
    if "--port" in argv:
        try:
            start_port = int(argv[argv.index("--port") + 1])
        except Exception:
            pass

    port = free_port(HOST, start_port)
    url = f"http://{HOST}:{port}"

    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer.request_queue_size = 64
    # small responses shouldn't wait on Nagle's algorithm — this is a loopback UI
    ThreadingHTTPServer.disable_nagle_algorithm = True
    srv = ThreadingHTTPServer((HOST, port), Handler)

    print(f"\n  TheDawg v{__version__}  —  {url}")
    print(f"  {DISTRO['pretty']}  ·  {detect_desktop_env()['raw'] or 'desktop'} "
          f"on {detect_desktop_env()['session']}")
    have = [PROVIDERS[pid]["label"] for pid in PROVIDERS if STATE["keys"].get(pid)]
    if have:
        print(f"  keys loaded for: {', '.join(have)}")
        def _warm():
            for pid in PROVIDERS:
                if STATE["keys"].get(pid):
                    fetch_models(pid, force=True)
        threading.Thread(target=_warm, daemon=True).start()
    else:
        print("  no API keys yet — add one in Settings")
    print(f"  active provider: {PROVIDERS[STATE['provider']]['label']}")
    print(f"  auto-test: up to {AUTOTEST_MAX_ROUNDS} silent fix rounds")

    # serve in the background so the GTK main loop can own the main thread
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5},
                     daemon=True).start()

    native = None
    if IS_LINUX and not force_browser:
        try:
            sys.path.insert(0, HERE)
            import shell as native_shell
            if native_shell.available():
                native = native_shell
            else:
                NATIVE_SHELL["reason"] = native_shell.missing_reason()
        except Exception as e:
            NATIVE_SHELL["reason"] = str(e)

    if native:
        NATIVE_SHELL.update({"active": True, "kind": "gtk4-webkit"})
        print("  window: native GTK4 + WebKitGTK")
        print("  serving local-only. close the window or ctrl-c to stop.\n")
        try:
            native.run(url,
                       config_dir=str(config_dir()),
                       data_dir=str(app_data_dir()),
                       icon_dir=os.path.join(HERE, "assets"),
                       on_quit=lambda: None,
                       dev=dev)
        except KeyboardInterrupt:
            pass
        print("\n  forge banked. later, dawg.\n")
        os._exit(0)

    if NATIVE_SHELL.get("reason") and IS_LINUX and not force_browser:
        print(f"  native window unavailable ({NATIVE_SHELL['reason']})")
        print(f"  install it:  {install_line('pygobject', 'gtk4', 'libadwaita', 'webkitgtk6')}")
    used = launch_browser_window(url)
    NATIVE_SHELL.update({"active": False, "kind": used or "browser"})
    if used:
        print(f"  window: {used} app mode")
    else:
        print("  window: plain browser tab")
    print("  serving local-only. ctrl-c to stop.\n")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n  forge banked. later, dawg.\n")
        srv.shutdown()


if __name__ == "__main__":
    main()
