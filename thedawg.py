#!/usr/bin/env python3
"""
TheDawg  -  AI-assisted Linux Python toolsmith
==============================================
A local workspace for building real, graphical Python tools by talking to a model —
GUI tools for Linux, tuned for Kali on Phosh (mobile GNOME, Wayland, OnePlus 6) and
KDE Plasma (X11, desktop). You agree on the tool in a build dialogue, TheDawg writes a
TESTING version you launch right here on YOUR box, you iterate on real behaviour, and
only when you ask does it package a RELEASE version. Then with one button it can also
pack the tool into a single-file Linux binary via PyInstaller.

Built for Linux from the ground up:
  - paths via pathlib + XDG dirs (~/.config, ~/.local/share)
  - process management with POSIX session/signal handling
  - GUI toolkits installed via pip into a managed venv
  - generated tools ship an install.sh (curl|bash) and a .desktop entry with an icon
  - the model is taught to write Linux GUI code that works under BOTH Wayland (Phosh)
    and X11 (KDE), and to stay responsive on a narrow phone screen

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

__version__ = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------
# PLATFORM DETECTION  -- every cross-platform branch in this file reads these
# --------------------------------------------------------------------------
IS_WIN = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"
IS_LINUX = not IS_WIN and not IS_MAC

def detect_desktop_env():
    """Classify the running Linux session so the UI can lay itself out for the
    right machine. Returns a dict the browser uses to pick a layout:

      {"de": "phosh"|"kde"|"gnome"|"other", "form": "mobile"|"desktop",
       "session": "wayland"|"x11"|"unknown", "raw": "<XDG_CURRENT_DESKTOP>"}

    Phosh (mobile GNOME shell on the OnePlus 6) -> a narrow, stacked, touch
    layout; KDE Plasma (and other desktops) -> the wide two-pane layout.

    Detection reads the freedesktop environment variables every session sets:
      - XDG_CURRENT_DESKTOP : ':'-joined desktop names (e.g. "Phosh:GNOME",
        "KDE", "GNOME"). This is the primary signal.
      - XDG_SESSION_TYPE / WAYLAND_DISPLAY : wayland vs x11.
      - PHOSH_* vars and the "phone" form factor hint Phosh exports.

    Everything degrades gracefully: on non-Linux, or when nothing is set, we
    report "other"/"desktop" so the wide layout is used. The browser can always
    override this with its own saved preference — this is only the default.
    """
    raw = os.environ.get("XDG_CURRENT_DESKTOP", "") or ""
    desk = raw.lower()
    sess = (os.environ.get("XDG_SESSION_TYPE", "") or "").lower()
    if not sess:
        sess = "wayland" if os.environ.get("WAYLAND_DISPLAY") else (
            "x11" if os.environ.get("DISPLAY") else "unknown")

    # Phosh sets XDG_CURRENT_DESKTOP to include "Phosh"; it also exports a few
    # PHOSH_* vars. Treat any of those as the mobile shell.
    is_phosh = ("phosh" in desk
                or any(k.startswith("PHOSH_") for k in os.environ)
                or os.environ.get("GNOME_SHELL_SESSION_MODE", "").lower() == "phosh")

    if is_phosh:
        de, form = "phosh", "mobile"
    elif "kde" in desk or "plasma" in desk:
        de, form = "kde", "desktop"
    elif "gnome" in desk:
        de, form = "gnome", "desktop"
    elif desk:
        de, form = "other", "desktop"
    else:
        de, form = "other", "desktop"

    return {"de": de, "form": form, "session": sess, "raw": raw}

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
# only on request. It targets Linux (Kali / Phosh / KDE Plasma). Tune to taste.
SYSTEM_PROMPT = """You are TheDawg, a senior Python engineer who builds small, sharp, genuinely
working GRAPHICAL (GUI) desktop tools for LINUX from a single-file script. The target machines are
Kali Linux on two setups: Phosh (the mobile GNOME shell, Wayland, on a OnePlus 6 — small touch
screen, portrait-friendly) and KDE Plasma on X11 (desktop, mouse + keyboard). Every tool you produce
opens a real window — never a bare command-line script. You write the kind of code a careful
professional ships: correct, defensive, readable, responsive. Hold yourself to that bar regardless
of how the request is phrased.

TOOLKIT (pick ONE per tool — the user is asked in the intake; honour their choice exactly):
- Tkinter — stdlib, no pip install (on Kali/Debian it needs the system package `python3-tk`,
  installed with `sudo apt install python3-tk`). The safest pick for "just works".
- CustomTkinter — modern, themed Tkinter (`pip install customtkinter`). Drop-in upgrade to
  Tkinter with a polished look. Use when the user wants something nicer than raw Tk.
- PyQt5 / PyQt6 / PySide6 — most polished and feature-rich; `pip install`. Qt sits most naturally
  on KDE Plasma and also renders well under Phosh. Best for serious, feature-rich tools.
- wxPython — only if explicitly requested; pip install but compilation can be slow.
Whatever you choose, stay on ONE toolkit for the whole tool. Never mix toolkits.

LINUX ENGINEERING (apply to EVERY tool — non-negotiable):
- Paths: ALWAYS use `pathlib.Path`/`os.path.join` — never hardcoded "/tmp/...". Use
  `tempfile.gettempdir()` for temp, `Path.home()` for home, `~/.config/<app>` for config and
  `~/.local/share/<app>` for data (honour `$XDG_CONFIG_HOME` / `$XDG_DATA_HOME` when set).
- Subprocesses: prefer a list argv (never shell=True with user input). Find external binaries with
  `shutil.which` and, if one is missing, show a clear in-window message naming the package and the
  `sudo apt install ...` line — never a silent failure or a raw traceback dialog.
- Encoding: pass `encoding="utf-8"` to `subprocess.run/Popen` text mode and to file `open()`.
- POSIX is fine to use directly (os.setsid, signal.SIGTERM, etc.) — no Windows guards needed. Do
  NOT import Windows-only modules (msvcrt, winreg, win32api) or write any Windows code paths.
- DISPLAY SERVER AWARENESS: the same code must run under BOTH Wayland (Phosh) and X11 (KDE). Don't
  assume one — never hardcode `DISPLAY=:0`, don't shell out to `xdotool`/`wmctrl` for core function
  (X11-only), and prefer the toolkit's own APIs for clipboard, screenshots, and window control so
  they work on both. If a feature is genuinely X11-only, detect `os.environ.get("WAYLAND_DISPLAY")`
  and degrade gracefully with an explanatory message.
- TOUCH + SMALL SCREEN (Phosh): the OnePlus 6 screen is narrow and portrait. Make windows resize
  cleanly down to ~360px wide, keep controls finger-sized, let content scroll vertically rather than
  needing a wide window, and never rely on hover-only interactions.

GUI ENGINEERING STANDARDS:
- It must actually open a window and do the agreed job when launched. Mentally trace startup,
  the main interaction, and the obvious failure paths before you output.
- RESPONSIVE LAYOUT. Set a sane default size (e.g. 720x560 desktop / comfortable when narrowed for
  Phosh), allow the window to resize, and let content scroll/reflow rather than clip. Use the
  toolkit's layout managers (grid, pack with expand=True, Qt layouts) — never hardcoded pixel
  positions that overflow on a different DPI.
- NEVER FREEZE THE UI. Any work that blocks — network calls, file scans, subprocess runs, large
  reads — MUST run off the main thread (`threading.Thread`) and marshal results back to the GUI
  thread safely (`widget.after` for Tkinter, signals for Qt). The window must stay responsive
  with a visible busy/progress state.
- DEGRADE GRACEFULLY IF THE TOOLKIT IS MISSING. At the very top, import the toolkit inside a
  try/except. On failure print to stderr the exact `pip install` line and exit non-zero:
      pip install customtkinter
      pip install PyQt5
      pip install PyQt6
      pip install PySide6
  For Tkinter on Linux, also note: `sudo apt install python3-tk` (or distro equivalent).
- IMPORT-SAFE STRUCTURE (so TheDawg can pre-check your code without opening a window): do ALL
  widget construction inside a class and/or a `main()` function, and only build+run it under
  `if __name__ == "__main__":`. Top-level code must be imports and definitions only — nothing
  that opens a window, connects to a display, or blocks at import time.
- Validate inputs in the UI: check fields, file existence, ranges, formats before acting; show
  the problem inline (entry styling, a label, or a dialog), don't crash.
- No silent failure, no bare `except: pass`, no placeholder/stub callbacks presented as working.
  No invented toolkit APIs — if unsure a method exists, use a standard approach you are sure of.
- Prefer the standard library for logic. The toolkit (Tk/Qt/etc.) is the only expected
  third-party dependency; if you genuinely need another package, put the exact install line on
  ONE line BEFORE the code block (always `pip install ...` — TheDawg installs into a managed venv).

RUNTIME CORRECTNESS — the bugs that slip past a parse/import check and only bite when the window
actually opens. TheDawg pre-checks your code by IMPORTING it, not by opening the window, so these
are exactly the mistakes that reach the user. Trace each one before you output:
- WIDGET REFERENCES THAT OUTLIVE THEIR SCOPE: any widget a callback or thread touches later must be
  stored on `self` (or captured in a closure that stays alive) — not a bare local that is garbage
  collected the moment the constructor returns. In Tkinter specifically, an image (`PhotoImage`,
  `ImageTk.PhotoImage`) MUST be kept on `self` or it is GC'd and the widget shows blank.
- THREAD → GUI MARSHALLING, every time: a worker thread must NEVER touch a widget directly. Tkinter:
  hand results back with `widget.after(0, lambda: ...)`. Qt: emit a signal connected to a main-thread
  slot, or use `QMetaObject.invokeMethod` — never call `setText`/`append` from the worker.
- CALLBACK ARGUMENTS: `command=` (Tk) and `clicked.connect` (Qt) pass specific args. Tk `command`
  takes none; Tk `bind` passes an event; Qt `clicked` passes a bool. Match the handler signature, or
  wrap in `lambda` to absorb/forward args. A late-binding loop (`for x in xs: btn(command=lambda: f(x))`)
  captures the LAST x — use `lambda x=x: f(x)`.
- LIFECYCLE: create exactly ONE root/QApplication; call `mainloop()` / `app.exec()` exactly once, at
  the end, under `__main__`. Don't create a second `Tk()` for a dialog (use `Toplevel`); don't call
  `mainloop()` from inside a callback.
- BLANK / FROZEN WINDOW: if the layout uses `pack`/`grid`, give expandable widgets `fill`/`expand` or
  `sticky` + row/column weights, or the window opens empty or unscrollable. Long startup work belongs
  in a thread with a visible "loading…" state, never inline in `__init__`.
- EXTERNAL PROCESS RESULTS: when wrapping a binary, capture BOTH stdout and stderr, decode with
  `encoding="utf-8", errors="replace"`, check the return code, and surface failures in the window —
  a non-zero exit with output only on stderr is the usual "it silently did nothing" bug.
- STATE AFTER ERRORS: re-enable buttons / hide spinners in a `finally`, so one failed run doesn't
  leave the UI stuck disabled.
Self-review pass before you finish: re-read your own code once and confirm every name is defined,
every `self.x` used in a callback was assigned in `__init__`, every function/method is called with
the right number of arguments, and no widget update happens off the main thread.

METHOD (the build dialogue):
1. CLARIFY BEFORE BUILDING. If meaningful details are unresolved, do not dump code — surface the
   decisions. (TheDawg may run a structured intake for you; honour every answer precisely,
   including the chosen toolkit.) Once the shape is clear, build.
2. TESTING VERSION BY DEFAULT: ONE complete, runnable, single-file GUI script. Lean but correct —
   real widgets, real behaviour, full input validation, threaded work, graceful errors — but no
   packaging ceremony yet.
3. ITERATE on real feedback: when given a run result/error/log, return the FULL updated script
   (never a diff) and state briefly what you changed and why.
4. RELEASE VERSION ONLY WHEN ASKED: top docstring with summary + how to launch on Linux, clean
   class structure, an optional minimal argparse for launch flags (e.g. --version) that does NOT
   replace the GUI, robust error handling, helpful comments, zero dead code. Still a GUI app.
5. SAFETY: no destructive operations (mass deletion, disk wipes, fork bombs) unless the user
   explicitly and unambiguously asks; if so, call it out. Assume it runs on the user's own machine.

OUTPUT FORMAT: a tight message first (a few sentences). THEN, only when actually providing code,
exactly ONE ```python fenced block with the entire single-file GUI script — never two blocks. When
only planning or discussing, include no code block at all."""

# Used to generate a tailored, clickable intake for a new tool request.
INTAKE_PROMPT = """You are the requirements analyst for TheDawg, a builder of GRAPHICAL (GUI) Python
tools for LINUX — specifically Kali on Phosh (mobile GNOME, Wayland, small touch screen) and KDE
Plasma (X11, desktop). The user wants to build a tool. Produce the SHORT, HIGH-VALUE set of
questions needed to build EXACTLY the right Linux GUI — no lazy or generic filler.

Return ONLY a JSON object, no prose, no markdown fences:
{"summary": "<one line restating the Linux GUI tool they want to build>",
 "questions": [
   {"q": "<clear question>", "options": ["<opt1>", "<opt2>", "<opt3>"], "multi": false},
   ...
 ]}

Rules:
- 3 to 6 questions MAX. Only ask what genuinely changes the code.
- ALWAYS include a toolkit question with options like ["Tkinter (stdlib, needs python3-tk)",
  "CustomTkinter (modern look, one pip install)", "PyQt5 / PySide6 (most polished)"] — pick the
  options that fit THIS tool. Tkinter is the safe default for simple tools; CustomTkinter when
  the user wants something prettier; Qt for serious feature-rich tools (sits well on KDE).
- Tailor the rest to THIS tool: what the main window shows (e.g. table of results, live log,
  form + output pane), what inputs the user gives (fields, file picker, target/range), whether it
  wraps an external Linux binary (and which one — many Kali tools shell out to things like nmap,
  tcpdump, aircrack-ng) or is pure-Python, and how results are presented/exported (in-window list,
  save to file, copy to clipboard).
- Do NOT ask which OS — it is always Linux. Only ask about an OS-feature when it matters (e.g.
  "show desktop notifications? — yes / no", or "does this need to run on the Phosh phone too?").
- 2 to 4 options per question. Options must be concrete and mutually distinct. Set "multi": true
  only when picking several genuinely makes sense.
- Prefer options the user can just tap. Keep them short."""

# Used by the GitHub-ready flow to assemble repo files from the user's answers.
GITHUB_PROMPT = """You are preparing a polished GitHub release of a LINUX Python GUI tool (targets
Kali on Phosh and KDE Plasma). You will be given the final code and the user's repo details.
Produce a complete, professional repo.

Return ONLY a JSON object, no prose, no markdown fences:
{"readme": "<full README.md markdown>",
 "gitignore": "<.gitignore contents>",
 "requirements": "<requirements.txt for pip deps, or empty string if pure stdlib>",
 "description": "<one-line repo description>"}

README requirements:
- Title, one-line description, then a short paragraph: what the GUI does and that it is a native
  Linux tool (tested on Kali / Phosh / KDE Plasma).
- A "Requirements" section listing Python ≥ 3.8 and the pip packages from requirements.txt (or
  noting "pure standard library" if there are none). If the tool uses Tkinter, note the system
  package `sudo apt install python3-tk`.
- An "Install" section with ONE one-line installer:
    curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/<branch>/install.sh | bash
  The same line should work for updates (re-running it). Use the exact user/repo/branch given.
- A "Usage" section: launch from the app grid / launcher, or by running `<name>` from a terminal,
  and a sentence on the main window. Keep it real and copy-pasteable.
- The license name. Clean, scannable, professional. No fluff.

For "requirements": detect imports beyond the stdlib in the code. Common entries: customtkinter,
PyQt5, PyQt6, PySide6, requests, etc. Tkinter is stdlib — do NOT list it. If pure stdlib, return an
empty string."""

# Used by the "review my code" button: a focused critique that DIAGNOSES, never rewrites.
REVIEW_PROMPT = """You are a senior Python/GUI engineer doing a careful code review of a single-file
Linux tool (Tkinter / CustomTkinter / PyQt / PySide), targeting Kali on Phosh (Wayland) and KDE
Plasma (X11). You are given the FULL code and, separately, the findings of an automated static
analyzer. Your job is to REVIEW, not rewrite — do NOT output a corrected script.

Look hard for things that will actually bite the user:
- logic errors and clashes: functions called with wrong/!args, methods that don't exist on the
  object, signals/callbacks wired to handlers that aren't defined, variables used before assignment
- GUI-specific problems: blocking work on the main thread (freezes the window), missing
  widget.after / signals when updating the UI from a thread, missing graceful handling when the
  toolkit isn't installed
- LINUX/DISPLAY: hardcoded paths like "/tmp"; missing encoding="utf-8" on text I/O; shell=True with
  user input; X11-only assumptions (xdotool/wmctrl, hardcoded DISPLAY) that break under Wayland on
  Phosh; layouts that can't narrow to a phone-width window
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

def library_save(name, code, messages, version="testing", args="", sid=None):
    """Snapshot a tool to the library at its CURRENT state: its code, the full build
    conversation, the version badge, and the test args. Reopening it restores all of
    that so you continue exactly where you left off — like saving a chat."""
    os.makedirs(LIBRARY_DIR, exist_ok=True)
    tid = _safe_id(name)
    rec = {"id": tid, "name": name or tid, "code": code,
           "messages": messages or [], "version": version or "testing",
           "args": args or "", "toolkit": (detect_toolkit(code or "") or {}).get("label"),
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

def session_save(sid, name, code, messages, version="testing", args=""):
    """Auto-save the live conversation+code for a tool in progress (its full state)."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    sid = sid or time.strftime("s%Y%m%d-%H%M%S")
    rec = {"id": sid, "name": name or "untitled", "code": code or "",
           "messages": messages or [], "version": version or "testing", "args": args or "",
           "toolkit": (detect_toolkit(code or "") or {}).get("label"),
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
# Maps a top-level import to (human label, pip package name, linux-specific apt hint).
# Everything is installable via pip on all three OSes. The apt hint is ONLY shown to
# Linux users in error messages — Tkinter on Linux often needs `python3-tk` as well
# because it's a C extension that Debian splits out of the base python3 package.
GUI_TOOLKITS = {
    "tkinter":      ("Tkinter",        None,             "python3-tk"),
    "customtkinter": ("CustomTkinter", "customtkinter",  "python3-tk"),  # needs Tk under the hood
    "PyQt5":        ("PyQt5",          "PyQt5",          None),
    "PyQt6":        ("PyQt6",          "PyQt6",          None),
    "PySide6":      ("PySide6",        "PySide6",        None),
    "PySide2":      ("PySide2",        "PySide2",        None),
    "wx":           ("wxPython",       "wxPython",       None),
}

def detect_toolkit(code):
    """Return the GUI toolkit a tool uses, or None.
    Result shape: {module, label, pip (pip package or None), apt_hint (Linux only)}."""
    tops = set()
    for m in re.finditer(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", code, re.M):
        tops.add(m.group(1).split(".")[0])
    for mod, (label, pip, apt_hint) in GUI_TOOLKITS.items():
        if mod in tops:
            return {"module": mod, "label": label, "pip": pip, "apt_hint": apt_hint}
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
    toolkit_mods = set(GUI_TOOLKITS.keys())
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
    pip packages installed here AND anything Python already has on this machine."""
    if not pkgs:
        return {"ok": True, "log": "no pip packages to install — already covered", "python": sys.executable}
    try:
        if not os.path.isdir(VENV_DIR):
            import venv
            # system_site_packages=True so the venv can still import packages already
            # available in the system Python (avoids re-installing things twice).
            venv.EnvBuilder(with_pip=True, system_site_packages=True).create(VENV_DIR)
        vpy = _venv_python() or sys.executable
        proc = subprocess.run([vpy, "-m", "pip", "install", "--upgrade", *pkgs],
                              capture_output=True, text=True, timeout=600,
                              encoding="utf-8", errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
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
        # Supplement it with our high-confidence self.<attr>-never-assigned pass so the
        # most common runtime AttributeError is caught regardless of which engine runs.
        supplemental = []
        try:
            import ast as _ast
            tree = _ast.parse(code)
            if not any(isinstance(n, _ast.ImportFrom) and any(a.name == "*" for a in n.names)
                       for n in _ast.walk(tree)):
                supplemental = _unassigned_self_attrs(tree)
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
            proc = subprocess.run([sys.executable, "-c", harness],
                                  capture_output=True, stdin=subprocess.DEVNULL, timeout=20)
            out = proc.stdout.decode("utf-8", errors="replace")
            err = proc.stderr.decode("utf-8", errors="replace")
            blob = out + "\n" + err
            if out.startswith("DEP_MISSING:"):
                note = "needs a package (use the deps button)"
                if tk:
                    hint = tk.get("apt_hint") or tk.get("pip") or "pip install"
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
                    "the right arguments and every name is defined before use.")
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
                    hint = ("\n[TheDawg] Tkinter is split out from Python on Debian-based distros "
                            "(including Kali). Install it:\n"
                            "  sudo apt install python3-tk     (Kali / Debian / Ubuntu / Mint)\n"
                            "  sudo dnf install python3-tkinter (Fedora)")
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
                f"(tested on Kali / Phosh / KDE Plasma).{pip_note}\n\n"
                f"## Usage\n\n```bash\n{launch_lin}\n```\n",
                encoding="utf-8")
        # .desktop entry so a GUI tool appears in the app menu / grid. StartupWMClass
        # helps Phosh and KDE bind the running window to this entry.
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
    (Phosh & KDE Plasma) and other Linux desktops."""
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
        f"A native **Linux** GUI tool — tested on Kali (Phosh & KDE Plasma).\n\n"
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
    # StartupWMClass helps Phosh/KDE bind the running window to this entry's icon.
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
    """Send the current code + the whole session log to the model for a fix."""
    if not SESSION_LOG:
        return {"error": "No runs logged yet — run the tool at least once first."}
    log_blob = render_log(full=False)
    convo = [m for m in messages if m.get("role") != "system"]
    convo = [{"role": "system", "content": SYSTEM_PROMPT}] + convo + [{
        "role": "user",
        "content": (
            "Here is the current tool and the full log of how it behaved when I ran it. "
            "Diagnose every problem you can see in the runs and return the FULL corrected "
            "script. Briefly list what you fixed.\n\n"
            f"=== CURRENT CODE ===\n```python\n{code}\n```\n\n"
            f"=== RUN LOG ===\n{log_blob}"
        )
    }]
    return chat_with_autotest(convo, provider_id)

def polish_round(code, messages, provider_id=None):
    """One iteration of the auto-polish loop: run a quick smoke, then ask the model
    to make the tool more robust/polished, returning improved code."""
    # smoke the current code so we can tell the model what's wrong right now
    passed, report, _ = smoke_test(code)
    state_note = "It passes a basic smoke test." if passed else f"It currently FAILS a check:\n{report}"
    log_blob = render_log(full=False) if SESSION_LOG else "(no runs yet)"
    convo = [{"role": "system", "content": SYSTEM_PROMPT}, {
        "role": "user",
        "content": (
            "Improve this tool by one meaningful increment: fix any bug, harden error "
            "handling, improve output clarity, and add the single most valuable missing "
            "feature — but keep it ONE self-contained script and don't over-engineer. "
            "Return the FULL improved script and one line on what you changed.\n\n"
            f"{state_note}\n\n=== CODE ===\n```python\n{code}\n```\n\n=== RECENT RUNS ===\n{log_blob}"
        )
    }]
    return chat_with_autotest(convo, provider_id)

# ==========================================================================
# http
# ==========================================================================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), ctype)
        except FileNotFoundError:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._file(os.path.join(HERE, "ui", "index.html"), "text/html; charset=utf-8")
        elif self.path.startswith("/assets/"):
            name = os.path.basename(self.path)
            ext = name.rsplit(".", 1)[-1].lower()
            ctype = {"svg": "image/svg+xml", "png": "image/png"}.get(ext, "application/octet-stream")
            self._file(os.path.join(HERE, "assets", name), ctype)
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
            self._file(full, ctype)
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
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
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
                                         data.get("args", ""), data.get("sessionId")))
        elif self.path == "/api/library/load":
            self._send(200, library_load(data.get("id", "")))
        elif self.path == "/api/library/delete":
            self._send(200, library_delete(data.get("id", "")))
        elif self.path == "/api/session/save":
            self._send(200, session_save(data.get("id"), data.get("name", "untitled"),
                                         data.get("code", ""), data.get("messages", []),
                                         data.get("version", "testing"), data.get("args", "")))
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
                             "desktop": detect_desktop_env()})
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
            if s.connect_ex((host, p)) != 0:
                return p
    return start

def launch_app_window(url):
    """Open TheDawg in a Chromium-family app window (no browser chrome).
    Falls back to a normal browser tab if no Chromium-family browser is found.
    Searches the right places on Windows, macOS, and Linux."""
    candidates = []
    if IS_WIN:
        # common install locations on Windows (Program Files + LocalAppData per-user installs)
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
        # Linux: rely on PATH lookups for the binaries
        for binname in ("chromium", "chromium-browser", "google-chrome",
                        "google-chrome-stable", "brave-browser", "microsoft-edge", "vivaldi"):
            p = shutil.which(binname)
            if p:
                candidates.append(p)

    app_data = str(app_data_dir() / "window")
    for path in candidates:
        if not path:
            continue
        # PATH-relative names → resolve them; absolute paths must exist
        resolved = path if os.path.isabs(path) else shutil.which(path)
        if not resolved or not os.path.exists(resolved):
            continue
        try:
            argv = [resolved, f"--app={url}",
                    f"--user-data-dir={app_data}",
                    "--no-first-run", "--no-default-browser-check",
                    # let TheDawg play its startup sound without needing a user gesture first.
                    # Chromium-family flag — safe on Chrome / Edge / Brave / Vivaldi / Chromium.
                    "--autoplay-policy=no-user-gesture-required",
                    "--window-size=1280,860"]
            # On Linux, set the window's WM class / Wayland app_id to "thedawg" so it
            # matches StartupWMClass in the .desktop entry. Without this the running
            # window shows a generic Chromium icon in the KDE Plasma task switcher and
            # the Phosh (mobile GNOME) overview instead of the Dawg icon.
            if IS_LINUX:
                argv.insert(1, "--class=thedawg")
            subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return os.path.basename(resolved)
        except Exception:
            continue
    # fallback: ordinary browser tab via webbrowser (handles every OS)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return None

def main():
    port = free_port(HOST, PORT)
    url = f"http://{HOST}:{port}"
    srv = ThreadingHTTPServer((HOST, port), Handler)
    print(f"\n  TheDawg v{__version__}  —  {url}")
    print(f"  Linux Python toolsmith  ·  running on {platform.system()}")
    have = [PROVIDERS[pid]["label"] for pid in PROVIDERS if STATE["keys"].get(pid)]
    if have:
        print(f"  keys loaded for: {', '.join(have)}")
        # fetch each keyed provider's live model catalog in the background so the
        # dropdown is accurate without blocking startup
        def _warm():
            for pid in PROVIDERS:
                if STATE["keys"].get(pid):
                    fetch_models(pid, force=True)
        threading.Thread(target=_warm, daemon=True).start()
    else:
        print("  no API keys yet — add one in Settings")
    print(f"  active provider: {PROVIDERS[STATE['provider']]['label']}")
    print(f"  auto-test: up to {AUTOTEST_MAX_ROUNDS} silent fix rounds")
    print("  serving local-only. ctrl-c to stop.\n")
    used = launch_app_window(url)
    if used:
        print(f"  opened in app window via {used}")
    else:
        print("  no Chromium-family browser found — opened a normal tab\n"
              "  (install Chrome/Edge/Brave for the clean app window)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  forge banked. later, dawg.\n")
        srv.shutdown()

if __name__ == "__main__":
    main()
