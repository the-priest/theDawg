<p align="center">
  <img src="assets/icon.png" alt="TheDawg" width="128" height="128">
</p>

<h1 align="center">TheDawg</h1>

<p align="center"><b>An AI Python toolsmith that runs as a real Linux app.</b></p>

Describe a tool in plain English. TheDawg agrees on the spec with you, forges a working
GUI application, lets you launch and test it on the spot, fixes its own bugs from the run
log, and — when it's right — packages it for GitHub or builds it into a single-file
executable. Everything runs locally; your API keys never leave your machine.

Tuned for **CachyOS**, and at home on any Arch, Debian, Fedora or SUSE box.

---

## Install

```
curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
```

Installs into `~/.local/share/thedawg`, drops a `thedawg` launcher on your `PATH`, and adds
an app-menu entry with the icon. Re-run it to update. No root needed.

The installer detects your distro, works out whether the native window is available, and
offers to install it if not. It writes the PATH line in the right syntax for your login
shell — including **fish**, which CachyOS ships by default on several editions.

Remove it with `install.sh --uninstall`.

### Check the machine

```
thedawg --doctor
```

Prints what's present, what's missing, and one copy-pasteable command in *your* package
manager's names to fix the lot. On CachyOS that's a `pacman` line, not an `apt` one.

---

## It's an app, not a browser window

TheDawg opens in a **GTK4 + libadwaita window** driving a WebKitGTK view. One process
(~150 MB instead of Chromium's ~500 MB), a real titlebar that follows your system theme, a
proper Wayland `app_id` so the icon and task switcher work on Plasma 6, native keyboard
shortcuts, and nothing written to a browser profile.

```
sudo pacman -S --needed python-gobject gtk4 libadwaita webkit2gtk-6.0
```

Without those it falls back to a Chromium app window, then to a plain browser tab — it
always runs, it just looks less like it belongs.

| shortcut | does |
|---|---|
| `Ctrl+R` / `F5` | reload |
| `Ctrl` `+` / `-` / `0` | zoom (remembered between sessions) |
| `F11` | fullscreen |
| `Ctrl+Shift+I` / `F12` | developer tools |
| `Ctrl+Q` | quit |

### Flags

```
thedawg                 launch
thedawg --doctor        check this machine, print exact install commands
thedawg --browser       force the browser front door
thedawg --safe-gfx      disable the WebKit dmabuf renderer (fixes a black window)
thedawg --dev           open with developer tools
thedawg --port N        start looking for a free port at N
```

If the window comes up black or blank, that's the known Mesa/NVIDIA dmabuf issue —
`thedawg --safe-gfx` is the fix.

---

## Set an API key

Set one for whichever provider you use, as an environment variable or in **Settings**:

| Provider          | Environment variable         | Notes                       |
|-------------------|------------------------------|-----------------------------|
| SiliconFlow       | `SILICONFLOW_API_KEY=sk-...` | Default — DeepSeek V4 Flash |
| Groq              | `GROQ_API_KEY=gsk_...`       | Fast, free tier             |
| Google AI Studio  | `GOOGLE_API_KEY=AIza...`     |                             |
| Novita AI         | `NOVITA_API_KEY=sk_...`      |                             |

Keys are stored in a per-user config file and are never sent to the browser. TheDawg pulls
each provider's live model list using your key, so the dropdown shows exactly what your
account can actually call.

---

## How it works

Four steps, shown along the top of the workspace:

1. **Agree** — You describe the tool. TheDawg asks a few sharp multiple-choice questions
   (tap to answer) or lays out a plan, so it builds what you meant rather than its best
   guess. You can skip the questions and just say "build it."
2. **Test** — It forges a **testing version** and you launch it with **▶ launch**. The tool
   opens its own window on your desktop. Nothing runs on its own. Or hit **🔎 self-test**
   and TheDawg runs the tool *itself* on a hidden display, screenshots it, checks it isn't
   blank, and clicks it — then tells you what's wrong without you typing a thing.
3. **Iterate** — **⮐ send log to AI & fix** diagnoses the run log plus the last self-test and
   patches the code. Or run the **✦ auto-polish loop**: each pass opens the tool, looks at
   it, pokes it, and feeds real crashes straight back.
4. **Release** — **◆ get ready for GitHub** assembles a full repo, or **⬛ build** packs it
   into a single-file binary.

### It knows which machine it's building for

The system prompt is built at startup from your actual distro. Ask for a Tkinter tool on
CachyOS and the generated code tells you `sudo pacman -S --needed tk`. The old build
hardcoded Debian package names into every tool it wrote, which were simply wrong here.

The host chip in the top bar shows what it detected. If that reads wrong, every install
hint the model gives will be wrong too.

Toolkits it can target: **PyQt6 / PySide6** (default for anything serious, best on KDE),
**GTK4 + libadwaita**, **CustomTkinter**, **Tkinter**.

---

## Features

**Building & iterating**
- Conversational build with structured intake — tappable multiple-choice spec questions.
- **Names itself, then locks.** Tracks a semantic version that bumps on every change. Click
  the name chip to lock a name of your own.
- **Auto-test**: after each build it silently checks the code and fixes failures for up to 3
  rounds before handing it back.
- **Completeness gate** — a script containing `# ... rest of the code unchanged` parses and
  imports perfectly, so it used to pass every check and reach you broken. Truncation markers
  and `TODO` stubs are now caught and fed straight back.
- **🔎 Self-test** — opens the window on a headless display, screenshots it, detects a blank
  window, and sends synthetic keys and a click to surface crash-on-interaction.
- Double-click a console error to send it straight to the model for a fix.

**Understanding the code**
- **🔍 Review** — combined AI + static (AST) analysis, rated by severity, with one-click fix.
- **⇄ Diff** — what changed between versions. **✎ Edit** — edit in-pane, model stays in sync.

**Dependencies & packaging**
- **⬇ Deps** — pip-installs into a managed venv that can still see your system packages, so
  a PyQt6 already installed by pacman isn't downloaded again. Uses `uv` when you have it.
- **⬛ Build** — single-file executable via PyInstaller.
- **◆ Get ready for GitHub** — README, `install.sh`, `LICENSE`, `.gitignore`, push commands.
  HTTPS remotes, never SSH.

**Keeping your work**
- **In progress** — auto-saves as you go. **★ Library** — save a tool at its exact state.
- **⤓ Save** / **⧉ copy** — drop the script to disk or clipboard anytime.
- Attach or drag in a `.py` to work on it, or logs / configs / CSV as context.

**Polish**
- Optional sound cues: drop `startup`, `done` and `build` audio files into
  `~/.local/share/thedawg/sounds/`.

---

## Speed

The UI now makes **zero external network requests**. It used to block first paint on Google
Fonts and a 120 KB highlight.js bundle from a CDN — in a local-only app, which meant that
with the network unplugged it stalled until DNS gave up. Both are gone: system fonts, and a
~2 KB purpose-built Python highlighter.

- HTTP/1.1 keep-alive, gzip, ETag revalidation and an in-memory static cache took the UI
  from 114,900 to 31,274 bytes on the wire.
- The ambient background pauses whenever the window isn't focused, and honours
  `prefers-reduced-motion`.
- Fonts are optional but nice: `sudo pacman -S --needed ttf-jetbrains-mono inter-font`

---

## Privacy & safety

- Local-only on `127.0.0.1` — nothing is exposed to your network.
- API keys live in a local config file and are never sent to the browser.
- Generated tools run on your machine as you. The danger guard flags destructive patterns
  before anything runs, but you are always the one who presses launch.

---

## A note on the artwork

The backdrop and the SEND button are South Park assets. Fine on your own machine, but not
yours to redistribute — `.gitignore` excludes them so they don't ship if you push this repo.
Without them TheDawg still runs; you just get the plain dark theme.

---

*Built on Kali. At home on CachyOS.*
