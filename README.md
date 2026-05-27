<p align="center">
  <img src="assets/icon-256.png" width="160" alt="TheDawg icon">
</p>

# TheDawg

**AI-assisted, cross-platform Python toolsmith.**
A local workspace for building small, sharp, *graphical* Python tools — that run natively on **Windows, Linux, and macOS** from the same single-file script. You talk to a model, agree on the tool, launch the testing version right here, iterate on real behaviour, and when it's good you either push a polished GitHub release or pack the whole thing into a standalone **`.exe` / binary** with one click.

It's the cross-platform sibling of [pysmith](https://github.com/the-priest/pysmith). Same care, same opinionated build loop — but the tools it builds aren't tied to any single OS.

---

## What you get

- **Real GUI tools, written for you.** Pick the toolkit per tool — Tkinter (zero install), CustomTkinter, PyQt5/6, PySide6, wxPython — TheDawg asks during intake and writes code that opens an actual window.
- **Runs everywhere from one script.** The system prompt is hard-wired to portability: `pathlib` everywhere, encoding pinned to UTF-8, no `os.fork` without a Windows guard, no `msvcrt` without a POSIX guard, no hardcoded `/tmp` or `C:\`, `shutil.which` for external binaries.
- **Talk first, then build.** A clickable intake surfaces the decisions that matter (toolkit, layout, inputs, exports) and feeds them to the model. No half-spec'd "here's some code, hope it works".
- **Auto-test, silently.** After every code reply, TheDawg runs Ruff (or an AST fallback), an import-safe smoke test, and a one-shot fix loop — so what lands in the editor is already syntactically clean and importable.
- **Launch + iterate locally.** ▶ Launch starts the window on *your* machine, ⤓ Log captures the run, ⊕ Fix from log hands the run output back to the model. No leaving the loop.
- **Ship it.** ◆ Get ready for GitHub assembles a polished repo with **both** `install.sh` (curl|bash for Linux/macOS) **and** `install.ps1` (iwr|iex for Windows), plus a README, .gitignore, requirements, LICENSE, and a `.desktop` entry.
- **⬛ Build executable** — *the killer feature*. One click runs PyInstaller in a managed venv and produces a single-file binary you can copy anywhere: `.exe` on Windows, native binaries on Linux/macOS.
- **Library + sessions.** ★ Library saves the tool and its whole build conversation so you can pick up exactly where you left off.
- **Four providers, real model lists.** Groq, SiliconFlow, Google, Novita. Keys live on *your* machine, never touch the browser.

---

## Install

### Linux / macOS

```bash
curl -fsSL https://raw.githubusercontent.com/the-priest/thedawg/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/the-priest/thedawg/main/install.ps1 | iex
```

Both installers are per-user, no admin/sudo needed. They drop a `thedawg` launcher on your PATH and (on Linux) a desktop entry so it appears in your app menu, (on Windows) a Start Menu shortcut.

### From a clone

```bash
git clone https://github.com/the-priest/thedawg
cd thedawg
./install.sh        # Linux / macOS
.\install.ps1       # Windows
```

---

## Set an API key

Pick whichever provider you have a key for — Groq has a generous free tier and is the recommended default:

**Linux / macOS:**

```bash
export GROQ_API_KEY="gsk_..."
```

**Windows (PowerShell):**

```powershell
$env:GROQ_API_KEY="gsk_..."
```

Or just launch TheDawg and paste the key into **⚙ Settings** — it persists to a per-user config file. Supported keys: `GROQ_API_KEY`, `SILICONFLOW_API_KEY`, `GOOGLE_API_KEY`, `NOVITA_API_KEY`.

---

## Launch

```bash
thedawg
```

Or pick **TheDawg** from your app menu (Linux) / Start Menu (Windows). It opens a clean Chromium-family app window pointed at `http://127.0.0.1:8765`.

---

## The ⬛ build button

Click **⬛ build** with a tool on the anvil and TheDawg will:

1. Lazily create a managed venv at `~/.local/share/thedawg/venv` (or `%APPDATA%\TheDawg\venv` on Windows).
2. `pip install` PyInstaller plus whatever your tool imports.
3. Run PyInstaller with `--onefile --windowed` and bundle the icon.
4. Drop the binary at `~/thedawg-tools/dist/<name>/out/<name>[.exe]`.

**Cross-compilation isn't supported** — PyInstaller bakes the host Python and libs into the output, so a Windows `.exe` really has to be built on Windows. Build on each target machine.

---

## Where things live

| Path | Linux / macOS | Windows |
|---|---|---|
| Config (keys, picked provider) | `~/.config/thedawg/config.json` | `%APPDATA%\TheDawg\config.json` |
| Library + sessions | `~/.local/share/thedawg/` | `%APPDATA%\TheDawg\` |
| Managed venv | `~/.local/share/thedawg/venv` | `%APPDATA%\TheDawg\venv` |
| Saved tools | `~/thedawg-tools/` | `%USERPROFILE%\TheDawg-tools\` |
| Built executables | `~/thedawg-tools/dist/<name>/out/` | `%USERPROFILE%\TheDawg-tools\dist\<name>\out\` |

---

## Sounds

Drop audio files into `sounds/` and TheDawg plays them at three moments:

| File              | Trigger                                      |
|-------------------|----------------------------------------------|
| `sounds/startup.mp3` | TheDawg opens                             |
| `sounds/done.mp3`    | The model lands a finished tool           |
| `sounds/build.mp3`   | ⬛ build succeeds                         |

`.mp3`, `.wav`, `.ogg`, `.m4a`, and `.flac` all work. Missing files are silent (no error). See `sounds/README.md` for the full notes.

---

## Requirements

- Python ≥ 3.8 (no other deps for TheDawg itself — stdlib only).
- For tools you build: whatever toolkit you pick. Tkinter is stdlib (on most Linux distros: `sudo apt install python3-tk`). The ⬇ deps button installs the rest into the managed venv for you.

---

## License

MIT. See [LICENSE](LICENSE).

Cartman-inspired icon is a homage, not endorsed by Comedy Central or anyone else.
