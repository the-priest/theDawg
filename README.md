<p align="center">
  <img src="assets/icon.png" alt="TheDawg" width="128" height="128">
</p>

<h1 align="center">TheDawg</h1>

<p align="center"><b>An AI Python GUI toolsmith for Linux &amp; macOS.</b></p>

Describe a tool in plain English. TheDawg agrees on the spec with you, forges a real
GUI application, lets you launch and test it on the spot, fixes its own bugs from the
run log, and — when it's right — packages it for GitHub or builds it into a single-file
executable. Everything runs locally; your API keys never leave your machine.

It opens in its own desktop app window (via your browser engine) and talks to a small
local-only Python server. No cloud backend, no telemetry.

---

## Install

```
curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
```

This installs into `~/.local/share/thedawg`, drops a `thedawg` launcher on your `PATH`,
and on Linux adds an app-menu entry with the icon. Running the command again updates
to the latest version.

Requires `python3` (3.8+). No root needed — everything lives under `$HOME`.

---

## Set an API key

TheDawg drives one of four model providers. Set a key for whichever you use, either as
an environment variable or in the in-app **Settings** panel:

| Provider          | Environment variable        | Notes                          |
|-------------------|-----------------------------|--------------------------------|
| Groq              | `GROQ_API_KEY=gsk_...`      | Recommended — fast, free tier  |
| SiliconFlow       | `SILICONFLOW_API_KEY=sk-...`| Default provider               |
| Google AI Studio  | `GOOGLE_API_KEY=AIza...`    |                                |
| Novita AI         | `NOVITA_API_KEY=sk_...`     |                                |

Keys are stored in a per-user config file on your machine and are never sent to the
browser. Each provider keeps its own key, and TheDawg pulls the live model list from
the provider using your key, so the model dropdown shows exactly what your account can
call.

---

## Launch

```
thedawg
```

Or pick **TheDawg** from your app menu (Linux). It prints a local URL, starts the
server, and opens an app window.

---

## How it works

The flow is four steps, shown along the top of the workspace:

1. **Agree** — You describe the tool. TheDawg either asks a few sharp multiple-choice
   questions (intake) or lays out a plan, so it builds exactly what you meant — not its
   best guess. You can skip the questions and just say "build it."
2. **Test** — It forges a **testing version** and you launch it right there with
   **▶ launch**. The tool opens its own window on your desktop. Nothing runs on its
   own — you press the button. Use **■ stop** to close it.
3. **Iterate** — When something breaks, hit **⮐ send log to AI & fix** and it diagnoses
   the run log and patches the code, or run the **✦ auto-polish loop** to launch → fix →
   improve over several passes automatically.
4. **Release** — When it's right, **◆ get ready for GitHub** polishes a clean release
   version and assembles a full repo, or **⬛ build** packs it into a single-file binary.

---

## Features

**Building & iterating**
- Conversational build with structured intake (multiple-choice spec questions up front).
- **Testing** vs **release** versions, tracked with a badge.
- **Auto-test**: after each build it silently runs the tool and fixes startup failures
  for up to 3 rounds before handing it back.
- **⮐ Send log to AI & fix** — diagnose and repair straight from the run output.
- **✦ Auto-polish loop** — automated launch → fix → improve cycles (safety-capped).
- Double-click a console error to send it straight to the model for a fix.

**Understanding the code**
- **🔍 Review** — combined AI + static (AST) analysis of the whole tool: finds clashes,
  bugs and risks, rates them by severity, and offers a one-click "fix these issues."
- **⇄ Diff** — see exactly what changed between the previous and current version.
- **✎ Edit** — edit the code yourself in-pane; the model stays in sync.

**Running safely**
- Launches real GUI windows, or captures stdout/stderr for CLI tools.
- **Danger guard** — if a draft matches destructive patterns, it's blocked until you
  read it and confirm. Everything runs locally as you, on `127.0.0.1` only.

**Dependencies & packaging**
- **⬇ Deps** — detects third-party imports (including the GUI toolkit) and pip-installs
  them into a managed virtualenv, so your system Python stays clean.
- **⬛ Build** — packages the tool into a single-file executable for your current OS with
  PyInstaller (windowed for GUIs, console option for CLIs). Copy that one file to another
  machine of the same OS/architecture and run it — no Python needed there.
- **◆ Get ready for GitHub** — polishes a release version and writes a complete repo:
  README with a one-line HTTPS install command, `install.sh`, `LICENSE` (MIT / GPLv3 /
  Apache-2.0 / none), `.gitignore`, and your push commands. Remotes use HTTPS, never SSH.

**Keeping your work**
- **In progress** — work auto-saves as you go; resume any tool exactly where you left off.
- **★ Library** — save a finished tool at its exact state (code + the whole build
  conversation + version + launch args); reopen it and keep iterating.
- **⤓ Save** / **⧉ copy** — drop the script to disk or the clipboard anytime.

**Bringing in your own files**
- Attach (or drag & drop) a `.py` to load it as the working tool, or attach logs,
  configs, JSON, CSV, etc. as context for what to build or fix.

**Polish**
- Optional sound cues: drop `startup`, `done`, and `build` audio files into
  `~/.local/share/thedawg/sounds/` and they play on those events.

---

## What to build first

Not sure where to start? Paste one of these into the build dialogue:

- **"A checksum verifier: I drag a file onto the window, it shows the MD5, SHA-1 and
  SHA-256, and lets me paste an expected hash to compare against with a green/red
  result."**

- **"A bulk file renamer with a live preview — pick a folder, set a find/replace or a
  numbered pattern, see the before/after list, and apply only when I confirm."**

- **"A subnet ping sweeper for my own home network: I enter a CIDR like 192.168.1.0/24,
  it pings each host and shows which are up, with hostnames where it can resolve them."**

- **"A markdown scratchpad with live preview side-by-side and autosave to a file, plus a
  word count in the status bar."**

- **"A QR code generator: type or paste text/a URL, see the QR update live, and save it
  as a PNG."**

Start small, launch it, then tell TheDawg what to change. Iterating is the whole point.

---

## Updating

Re-run the installer — it always pulls the latest:

```
curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
```

## Uninstall

```
rm -rf ~/.local/share/thedawg ~/.local/bin/thedawg ~/.local/share/applications/thedawg.desktop
```

(Icons under `~/.local/share/icons/hicolor/*/apps/thedawg.*` can be removed too.)

---

## Privacy & safety

- Runs local-only on `127.0.0.1` — nothing is exposed to your network.
- API keys live in a local config file and are never sent to the browser.
- Generated tools execute on your machine as you; the danger guard flags destructive
  patterns before anything runs, but you are always the one who presses launch.

---

*Built on Kali, at home anywhere with Python.*
