<p align="center">
  <img src="assets/icon.png" alt="TheDawg" width="132" height="132">
</p>

<h1 align="center">TheDawg</h1>

<p align="center">
  <b>Describe a tool in plain English. Watch it get built, tested, and fixed — live.</b>
</p>

<p align="center">
  <img alt="platform" src="https://img.shields.io/badge/Linux-CachyOS%20%C2%B7%20Arch%20%C2%B7%20Debian%20%C2%B7%20Fedora%20%C2%B7%20SUSE-1793d1?style=flat-square&logo=linux&logoColor=white">
  <img alt="python" src="https://img.shields.io/badge/Python-3.10%2B-e8a33d?style=flat-square&logo=python&logoColor=white">
  <img alt="ui" src="https://img.shields.io/badge/UI-GTK4%20%2B%20WebKitGTK-9fe04a?style=flat-square">
  <img alt="license" src="https://img.shields.io/badge/License-MIT-46c7d4?style=flat-square">
  <img alt="local" src="https://img.shields.io/badge/100%25-local-c79be0?style=flat-square">
</p>

<p align="center">
  <i>An AI Python toolsmith that runs as a real Linux app — not a browser tab, not a cloud service.</i>
</p>

---

TheDawg agrees on the spec with you, forges a working **GUI application**, and shows you
**exactly what it's doing while it does it** — planning, writing, testing, and fixing its
own bugs, step by step. You launch and test the tool on the spot, and when it's right,
TheDawg packages it for GitHub or builds it into a single-file executable.

Everything runs **locally**. Your API keys never leave your machine.

<br>

<table>
<tr>
<td width="50%" valign="top">

**🔨 You describe it**
> *"A checksum verifier — drag a file on, show MD5/SHA-256, paste a hash to compare."*

**👁 You watch it build**
> Planning → writing → testing → *caught 2 issues* → fixing → ✓ passed — in real time, with the model's own reasoning shown.

</td>
<td width="50%" valign="top">

**▶ You test it**
> One click launches the real window. Or hit **self-test** and TheDawg runs it on a hidden display and tells you what's broken.

**◆ You ship it**
> A full GitHub repo, or a single-file Linux binary.

</td>
</tr>
</table>

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
```

Installs into `~/.local/share/thedawg`, drops a `thedawg` launcher on your `PATH`, and adds
an app-menu entry with the icon. **No root needed.** Re-run to update; `install.sh --uninstall`
to remove.

The installer detects your distro, sorts out the native window, and writes the `PATH` line in
the right syntax for your login shell — including **fish**, which CachyOS ships by default.

> [!TIP]
> Run `thedawg --doctor` to see what's present, what's missing, and one copy-pasteable command
> in **your** package manager's names to fix the lot. On CachyOS that's a `pacman` line, not `apt`.

---

## ✨ New in 2.4 — the makeover

<table>
<tr><td>

- **🏔 The town shows through.** Frosted-glass panels and translucent chat bubbles let the
  snowy backdrop read through the whole workshop, with a slow aurora drifting over it.
- **👁 Code hides behind a button.** The workspace shows the *build*, not a wall of source.
  One click reveals the syntax-highlighted code; one click hides it again.
- **📡 Watch the AI work, live.** A streaming activity feed shows every real stage as it
  happens — planning, the model's **own reasoning**, writing, each auto-test round, and the
  **exact problems** it caught and fixed.
- **🔔 The done chime plays** on every finished build, on auto-polish converging, and on a fix landing.
- **🩺 Errors say what's wrong.** A crash is parsed into `KeyError · line 18 in compute()` with
  a plain-English cause — not a raw traceback.

</td></tr>
</table>

---

## It's an app, not a browser window

TheDawg opens in a **GTK4 + libadwaita** window driving a WebKitGTK view: one process
(~150 MB vs Chromium's ~500 MB), a real titlebar that follows your system theme, a proper
Wayland `app_id` so the icon and task switcher work on Plasma 6, native shortcuts, and
nothing written to a browser profile.

```bash
sudo pacman -S --needed python-gobject gtk4 libadwaita webkit2gtk-6.0
```

Without those it falls back to a Chromium app window, then a plain browser tab — it always
runs, it just looks less like it belongs.

<details>
<summary><b>Keyboard shortcuts & command-line flags</b></summary>

<br>

| shortcut | does |
|---|---|
| `Ctrl+R` / `F5` | reload |
| `Ctrl` `+` / `-` / `0` | zoom (remembered between sessions) |
| `F11` | fullscreen |
| `Ctrl+Shift+I` / `F12` | developer tools |
| `Ctrl+Q` | quit |

```bash
thedawg                 # launch
thedawg --doctor        # check this machine, print exact install commands
thedawg --browser       # force the browser front door
thedawg --safe-gfx      # disable the WebKit dmabuf renderer (fixes a black window)
thedawg --dev           # open with developer tools
thedawg --port N        # start looking for a free port at N
```

> If the window comes up black or blank, that's the known Mesa/NVIDIA dmabuf issue —
> `thedawg --safe-gfx` is the fix.

</details>

---

## Set an API key

Set one for whichever provider you use — as an environment variable or in **Settings**:

| Provider | Environment variable | Notes |
|---|---|---|
| **SiliconFlow** | `SILICONFLOW_API_KEY=sk-...` | **Default** — DeepSeek V4 Flash |
| Groq | `GROQ_API_KEY=gsk_...` | Fast, free tier |
| Google AI Studio | `GOOGLE_API_KEY=AIza...` | |
| Novita AI | `NOVITA_API_KEY=sk_...` | |

Keys are stored in a per-user config file and are **never sent to the browser**. TheDawg
pulls each provider's live model list using your key, so the dropdown shows exactly what
your account can call. Pick any model in Settings and it sticks — for every call, not just some.

---

## How it works

Four steps, shown along the top of the workspace:

### 1 · Agree
You describe the tool. TheDawg asks a few sharp multiple-choice questions (tap to answer) or
lays out a plan, so it builds what you *meant* rather than its best guess. Skip the questions
and just say **"build it"** anytime.

### 2 · Test
It forges a **testing version**. Hit **▶ launch** and the tool opens its own window on your
desktop — nothing runs on its own, you press the button. Or hit **🔎 self-test** and TheDawg
runs the tool *itself* on a hidden display, screenshots it, checks it isn't blank, and clicks
it — then tells you what's wrong without you typing a thing.

### 3 · Iterate
**⮐ send log to AI & fix** diagnoses the run log plus the last self-test and patches the code.
Or run the **✦ auto-polish loop**: each pass opens the tool, looks at it, pokes it, and feeds
real crashes straight back — stopping the moment it converges.

### 4 · Release
**◆ get ready for GitHub** assembles a full repo (README, `install.sh`, `LICENSE`,
`.gitignore`, push commands — HTTPS remotes, never SSH). Or **⬛ build** packs it into a
single-file Linux binary with PyInstaller.

> [!NOTE]
> **It knows which machine it's building for.** The system prompt is built at startup from
> your actual distro. Ask for a Tkinter tool on CachyOS and the generated code tells you
> `sudo pacman -S --needed tk` — not the wrong Debian name. The **host chip** in the top bar
> shows what it detected.

**Toolkits it can target:** PyQt6 / PySide6 (default for anything serious, best on KDE),
GTK4 + libadwaita, CustomTkinter, Tkinter.

---

## Features

<table>
<tr>
<td width="50%" valign="top">

**🔨 Building & iterating**
- Conversational build with tappable multiple-choice intake
- **Names itself, then locks** — semantic version bumps on every change
- **Auto-test**: silently checks each build and fixes failures for up to 3 rounds
- **Completeness gate** catches `# ... rest unchanged` truncation and `TODO` stubs before they reach you
- **🔎 Self-test** on a headless display — blank-window detection + synthetic clicks to surface crashes
- Double-click a console error to send it straight for a fix

**🔍 Understanding the code**
- **Review** — combined AI + static (AST) analysis, rated by severity, one-click fix
- **Diff** — a real line-by-line diff between versions
- **Edit** — edit in-pane, the model stays in sync

</td>
<td width="50%" valign="top">

**📦 Dependencies & packaging**
- **Deps** — pip-installs into a managed venv that still sees your system packages (a pacman-installed PyQt6 isn't re-downloaded); uses `uv` when present
- **Build** — single-file executable via PyInstaller
- **GitHub** — full repo scaffold, HTTPS remotes

**💾 Keeping your work**
- **In progress** auto-saves as you go
- **★ Library** saves a tool at its exact state
- **Save** / **copy** to disk or clipboard anytime
- Drag in a `.py` to work on, or logs / configs / CSV as context

**🔊 Polish**
- Optional sound cues — drop `startup`, `done`, `build` audio into `~/.local/share/thedawg/sounds/`

</td>
</tr>
</table>

---

## Models & spend

**DeepSeek V4 Flash is the default** — strong, fast, and roughly a fifth of Pro's price — and
your choice in **Settings** applies to *every* call. Want Pro for the heavy build work? Pick it,
and it sticks.

TheDawg keeps a 1M-token budget lasting by working smart, not just cheap:

- **Targeted edits.** A one-line change asks the model for a small search/replace patch instead
  of retyping the whole file — **4–8× fewer output tokens** on iterate and fix rounds. If a patch
  doesn't apply cleanly it falls back to a full rewrite, and a circuit breaker disables the whole
  scheme if a model can't produce the format.
- **Free local analysis** catches wrong argument counts, unknown keywords and mutable defaults
  — the most common way generated code parses fine and then dies at runtime. Every one caught
  locally is a paid fix round that never happens.
- **Superseded code and stale attachments are collapsed** out of the conversation, so a long
  session doesn't resend ten versions of the same growing file, or a 60 KB sample CSV every turn.
- **Reply length is capped per tier**, so a rambling model can't run up a bill.

> **Watch it happen.** The top bar shows tokens and a running cost estimate (with a `~` when a
> provider doesn't report usage); `clidawg /cost` breaks it down per model. Figures are list-rate
> indicators, not an invoice.

---

## Also: CLI Dawg

A terminal front end that shares the same engine — builds **command-line tools** by default
(argparse, exit codes, pipe-friendly stdout), `/gui` to switch to GUI apps. Runs as a Textual
TUI, a `--plain` REPL, or one-shot: `clidawg build "a tool that ..."`.

---

## Speed & privacy

- **Zero external network requests** from the UI — system fonts and a ~2 KB purpose-built
  Python highlighter replaced Google Fonts and a 120 KB CDN highlighter that used to stall
  first paint with the network unplugged.
- HTTP/1.1 keep-alive, gzip, ETag revalidation and an in-memory static cache; the background
  pauses when unfocused and honours `prefers-reduced-motion`.
- Fonts are optional but nice: `sudo pacman -S --needed ttf-jetbrains-mono inter-font`
- **Local-only** on `127.0.0.1` — nothing is exposed to your network. Keys stay in a local
  config file. Generated tools run on your machine as you; the danger guard flags destructive
  patterns before anything runs on **all three** execution paths (launch, self-test, and the
  silent smoke test) — but you're always the one who presses launch.

---

<p align="center"><i>Built on Kali. At home on CachyOS.</i></p>
