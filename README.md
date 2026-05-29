<p align="center">
  <img src="assets/icon-256.png" width="160" alt="TheDawg icon">
</p>

# TheDawg

**AI-assisted Linux Python toolsmith.**
A local workspace for building small, sharp, *graphical* Python tools that run natively on **Linux** — tuned for **Kali** on **Phosh** (the mobile GNOME shell, on a OnePlus 6) and **KDE Plasma** (X11, desktop). You talk to a model, agree on the tool, launch the testing version right here, iterate on real behaviour, and when it's good you either push a polished GitHub release or pack the whole thing into a standalone single-file **Linux binary** with one click.

It's the Linux-focused sibling of [pysmith](https://github.com/the-priest/pysmith). Same care, same opinionated build loop.

---

## What you get

- **Real GUI tools, written for you.** Pick the toolkit per tool — Tkinter (`python3-tk`), CustomTkinter, PyQt5/6, PySide6, wxPython — TheDawg asks during intake and writes code that opens an actual window.
- **Built for your machines.** The system prompt is hard-wired to Linux: `pathlib` + XDG dirs, UTF-8 everywhere, `shutil.which` for external binaries, and code that works under **both** Wayland (Phosh) and X11 (KDE) — no X11-only assumptions, and layouts that narrow cleanly to a phone-width screen.
- **Talk first, then build.** A clickable intake surfaces the decisions that matter (toolkit, layout, inputs, exports) and feeds them to the model.
- **Auto-test, silently.** After every code reply, TheDawg runs Ruff (or an AST fallback — now including a self-attribute check that catches AttributeError-at-click bugs), an import-safe smoke test, and a multi-round fix loop — so what lands in the editor is already clean and importable.
- **Launch + iterate locally.** Launch starts the window on *your* machine, Log captures the run, Fix from log hands the run output back to the model.
- **Ship it.** Get ready for GitHub assembles a polished repo with `install.sh` (curl|bash), a README, .gitignore, requirements, LICENSE, and a `.desktop` entry with the Dawg icon.
- **Build binary** — one click runs PyInstaller in a managed venv and produces a single-file Linux binary you can copy to another Linux box (same architecture) and run with no Python installed.
- **Library + sessions.** Library saves the tool and its whole build conversation so you can pick up exactly where you left off.
- **Providers.** SiliconFlow (primary, **DeepSeek V4 Flash** as the default model) with Groq as an automatic fallback; Google and Novita also available. Keys live on *your* machine, never touch the browser.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
```

Per-user, no sudo needed. It drops a `thedawg` launcher on your PATH, installs the Dawg icon in every size (plus a scalable copy) so it's crisp on Phosh and KDE, and registers a `.desktop` entry so TheDawg shows up in your app grid / launcher.

### From a clone

```bash
git clone https://github.com/the-priest/theDawg
```

```bash
cd thedawg
```

```bash
./install.sh
```

---

## Set an API key

SiliconFlow is the default provider, with DeepSeek V4 Flash as the primary model (1M context, fast, cheap). Groq is the automatic fallback.

```bash
export SILICONFLOW_API_KEY="sk-..."
```

```bash
export GROQ_API_KEY="gsk_..."
```

Or just launch TheDawg and paste the key into **Settings** — it persists to a per-user config file. Supported keys: `SILICONFLOW_API_KEY`, `GROQ_API_KEY`, `GOOGLE_API_KEY`, `NOVITA_API_KEY`.

---

## Launch

```bash
thedawg
```

Or pick **TheDawg** from your app grid / launcher. It opens a clean Chromium-family app window pointed at `http://127.0.0.1:8765`.

---

## The build button

Click **build** with a tool on the anvil and TheDawg will:

1. Lazily create a managed venv at `~/.local/share/thedawg/venv`.
2. `pip install` PyInstaller plus whatever your tool imports.
3. Run PyInstaller with `--onefile --windowed` and bundle the icon.
4. Drop the binary at `~/thedawg-tools/dist/<name>/out/<name>`.

The binary is a Linux executable: copy it to any Linux machine of the same architecture and run it directly — no Python needed there.

---

## Where things live

| Path | Location |
|---|---|
| Config (keys, picked provider) | `~/.config/thedawg/config.json` |
| Library + sessions | `~/.local/share/thedawg/` |
| Managed venv | `~/.local/share/thedawg/venv` |
| Saved tools | `~/thedawg-tools/` |
| Built binaries | `~/thedawg-tools/dist/<name>/out/` |

---

## Sounds

Drop audio files into `sounds/` and TheDawg plays them at three moments:

| File              | Trigger                                      |
|-------------------|----------------------------------------------|
| `sounds/startup.mp3` | TheDawg opens                             |
| `sounds/done.mp3`    | The model lands a finished tool           |
| `sounds/build.mp3`   | build succeeds                            |

`.mp3`, `.wav`, `.ogg`, `.m4a`, and `.flac` all work. Missing files are silent (no error).

---

## Requirements

- Python >= 3.8 (no other deps for TheDawg itself — stdlib only).
- For tools you build: whatever toolkit you pick. Tkinter needs `sudo apt install python3-tk` on Kali/Debian. The deps button installs pip toolkits into the managed venv for you.

---

## License

MIT. See [LICENSE](LICENSE).

Cartman-inspired icon is a homage, not endorsed by Comedy Central or anyone else.
