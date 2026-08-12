#!/usr/bin/env python3
"""
CLI Dawg  —  TheDawg for the terminal
=====================================
Same forge, no window. You describe a tool, CLI Dawg agrees on the shape, writes
it, runs it, reads the failure, and fixes it — all inside your shell.

Where TheDawg builds GUI apps, CLI Dawg builds **command-line tools** by default:
argparse, honest exit codes, pipe-friendly stdout, colour only when attached to a
terminal. That's the point of it — a terminal toolsmith that makes terminal tools.
`/gui` switches it over to building windowed tools if you want that instead.

Three ways to run it:

    clidawg                          full-screen TUI (Textual)
    clidawg --plain                  line-by-line REPL — works over ssh, serial,
                                     dumb terminals, or anywhere Textual isn't
    clidawg build "a port scanner"   one-shot: build, test, save, print the path

The engine — providers, the distro-aware prompt, the auto-test loop, the
completeness gate, dependency handling, packaging — is imported from thedawg.py
rather than reimplemented, so both front ends stay in lockstep and a fix in one
is a fix in both.

License: MIT
"""

import argparse
import os
import sys
import textwrap
import threading
import time
from pathlib import Path

__version__ = "1.1.3"
HERE = Path(__file__).resolve().parent


# ==========================================================================
# ENGINE
# ==========================================================================
def load_engine():
    """Import thedawg.py as the shared engine.

    It lives next to this file in a normal install. We also check the standard
    install dir so `clidawg` still works if it's been copied onto the PATH by
    itself. Importing is side-effect-safe: thedawg guards its own main().
    """
    candidates = [HERE, Path.home() / ".local" / "share" / "thedawg"]
    for c in candidates:
        if (c / "thedawg.py").is_file():
            if str(c) not in sys.path:
                sys.path.insert(0, str(c))
            try:
                import thedawg
                return thedawg
            except Exception as e:                      # pragma: no cover
                print(f"cli dawg: found thedawg.py in {c} but couldn't load it:\n  {e}",
                      file=sys.stderr)
                raise SystemExit(1)
    print(textwrap.dedent("""
        cli dawg: can't find the engine (thedawg.py).

        It should sit next to this script, or in ~/.local/share/thedawg.
        Install both with:
          curl -fsSL https://raw.githubusercontent.com/the-priest/theDawg/main/install.sh | bash
    """).strip(), file=sys.stderr)
    raise SystemExit(1)


core = load_engine()


# ==========================================================================
# THE PROMPT  --  terminal tools, not windows
# ==========================================================================
CLI_PROMPT_TMPL = """You are CLI Dawg, a senior Python engineer who builds small, sharp,
genuinely working COMMAND-LINE tools as a single-file script. The machine you are building for
RIGHT NOW is: __DISTRO_PRETTY__ (__DISTRO_FAMILY__ family, package manager `__PKG_MGR__`).
Target that box first and stay portable to other Linux systems.

Every tool you produce is run from a shell and behaves like it belongs there. No GUI, no window,
no toolkit. Write the code a careful professional ships: correct, defensive, readable — and a good
citizen of the pipeline.

THE SHAPE OF EVERY TOOL:

    #!/usr/bin/env python3
    \"\"\"<toolname> — one-line summary.\"\"\"
    import argparse, sys
    ...

    def build_parser():
        p = argparse.ArgumentParser(
            prog="<toolname>",
            description="<what it does>",
            epilog="examples:\\n  <toolname> <typical invocation>",
            formatter_class=argparse.RawDescriptionHelpFormatter)
        p.add_argument(...)
        return p

    def main(argv=None):
        args = build_parser().parse_args(argv)
        try:
            ...
        except KeyboardInterrupt:
            return 130
        return 0

    if __name__ == "__main__":
        sys.exit(main())

Top level holds imports, constants and definitions ONLY — nothing that runs, prompts, or blocks at
import time. CLI Dawg imports your module to pre-check it before you ever see output.

COMMAND-LINE ENGINEERING — non-negotiable:
- EXIT CODES ARE THE API. 0 success, non-zero failure. 2 for bad usage (argparse does this), 130
  for Ctrl-C. A script that prints "error" and exits 0 is broken — it lies to `&&` and to CI.
- STDOUT IS DATA, STDERR IS TALK. Results, and only results, go to stdout so the tool can be
  piped. Progress, warnings and errors go to stderr. `--quiet` silences stderr chatter, never the
  data.
- PIPES: read stdin when the input argument is `-` or absent and stdin isn't a tty. Handle
  BrokenPipeError cleanly so `| head` doesn't produce a traceback.
- COLOUR ONLY WHEN IT'S A TERMINAL: gate every escape code behind
  `sys.stdout.isatty()` and honour the `NO_COLOR` environment variable. Never emit ANSI into a
  redirected file.
- `--help` MUST BE GENUINELY USEFUL: a real description, sensible metavars, and at least one
  worked example in the epilog. Add `--version`.
- MACHINE-READABLE OPTION: if the tool emits structured results, offer `--json`. Humans get the
  pretty table, scripts get the JSON, nobody has to parse your columns.
- LONG WORK GETS FEEDBACK on stderr (a counter or a progress line), and must stay interruptible —
  Ctrl-C exits promptly and cleanly, without a traceback.
- PATHS: `pathlib.Path` always. `~/.config/<app>` for settings, `~/.local/share/<app>` for data,
  honouring `$XDG_CONFIG_HOME` / `$XDG_DATA_HOME`. Never hardcode "/tmp/..." or "/home/user".
- SUBPROCESSES: list argv, never `shell=True` with user input. Locate binaries with `shutil.which`
  and, when one is missing, print the exact command for THIS distro to stderr and exit non-zero:
  `__PKG_MGR__ <package>`.
- ENCODING: `encoding="utf-8"` on every `open()` and text-mode `subprocess` call, with
  `errors="replace"` when reading foreign output.
- VALIDATE INPUT before doing work: check files exist, ranges make sense, formats parse. Fail with
  a clear one-line message on stderr naming what was wrong, not a traceback.
- CONCURRENCY where it obviously pays (network sweeps, per-file hashing): a bounded
  `ThreadPoolExecutor`, never an unbounded thread per item.

BANNED — any of these makes the output wrong:
- Truncating the script. No "# ... rest unchanged", no "# (previous code here)", no `...` standing
  in for real code. Every iteration returns the WHOLE file.
- `except: pass` swallowing an error the user needed to see.
- Stub functions, `pass  # TODO`, or fake data presented as a working result.
- `print()` for errors (that's stdout — use stderr) or `sys.exit("message")` where an exit code
  was meant.
- Invented APIs. If you aren't certain a function exists, use one you are certain of.
- A second code block. Exactly one ```python block per reply, or none at all.

FINAL SELF-CHECK before you output:
1. Every name used is defined; every function is called with the right arguments.
2. `--help` runs and reads well. `--version` works. Bad input exits 2, not 1 or 0.
3. Results go to stdout; everything else goes to stderr.
4. Ctrl-C during the slow part exits 130 without a traceback.
5. The script is complete from first line to last.
6. Trace it once: normal run, empty input, and one failure path.

METHOD:
1. CLARIFY FIRST if meaningful decisions are open — prefer concrete either/or questions the user
   can answer in a word. Once the shape is clear, build.
2. TESTING VERSION BY DEFAULT: one complete runnable single-file script.
3. ITERATE on real feedback: given a run result or traceback, return the FULL updated script and
   say briefly what changed.
4. RELEASE VERSION ONLY WHEN ASKED: top docstring, clean structure, useful comments, no dead code.
5. SAFETY: no destructive operations unless asked explicitly and unambiguously — and then say so.

OUTPUT FORMAT: a tight message first (a few sentences — what you built or changed). THEN, only
when actually providing code, exactly ONE ```python fenced block with the entire single-file
script. When planning or asking, include no code block at all."""


def build_cli_prompt(gui=False):
    """Bake this machine's real package manager into the prompt.

    With gui=True we hand straight over to TheDawg's GUI prompt, so `/gui` gives
    you the exact same builder the desktop app uses — no second-rate copy.
    """
    if gui:
        return core.SYSTEM_PROMPT
    d = core.DISTRO
    return (CLI_PROMPT_TMPL
            .replace("__DISTRO_PRETTY__", d.get("pretty") or "Linux")
            .replace("__DISTRO_FAMILY__", d.get("family") or "other")
            .replace("__PKG_MGR__", d.get("install") or "your package manager"))


# ==========================================================================
# SESSION  --  all state and every action, with no opinion about the UI.
# Both front ends drive this same object, so the TUI and the REPL can never
# drift apart in behaviour.
# ==========================================================================
class Session:
    def __init__(self, gui=False):
        self.messages = []          # the build dialogue (no system message here)
        self.code = ""              # current tool source
        self.name = "untitled"
        self.ver = "1.0.0"
        self.stage = "testing"      # testing | release
        self.gui = gui
        self.last_run = None
        self.pid = None

    # -- helpers ----------------------------------------------------------
    def _system(self):
        return {"role": "system", "content": build_cli_prompt(self.gui)}

    def _bump(self):
        try:
            a, b, c = (self.ver.split(".") + ["0", "0"])[:3]
            self.ver = f"{a}.{b}.{int(c) + 1}"
        except Exception:
            self.ver = "1.0.1"

    def _name_from_code(self, code):
        """Take the tool's name from its own argparse prog= or its docstring."""
        import re
        m = re.search(r'prog\s*=\s*["\']([\w.-]+)["\']', code)
        if m:
            return m.group(1).replace(".py", "")
        m = re.search(r'^\s*"""\s*([\w][\w .-]{0,40}?)\s*[—\-:]', code, re.M)
        if m:
            return m.group(1).strip().lower().replace(" ", "_")
        return self.name

    @property
    def filename(self):
        return f"{self.name}.py"

    # -- the build turn ---------------------------------------------------
    def send(self, text):
        """One turn of the dialogue. Returns a result dict for the UI to render.

        Delegates to the engine's chat_with_autotest, which silently smoke-tests
        whatever the model returns, catches truncated or stubbed scripts, and
        feeds failures back for up to three rounds before we ever see it.
        """
        self.messages.append({"role": "user", "content": text})
        convo = [self._system()] + self.messages
        res = core.chat_with_autotest(convo)
        if res.get("error"):
            self.messages.pop()
            return {"error": res["error"]}

        reply = res.get("reply", "")
        self.messages.append({"role": "assistant", "content": reply})

        code = core.extract_code(reply)
        if code:
            first = not self.code
            self.code = code
            if first:
                self.name = self._name_from_code(code)
            else:
                self._bump()
        return {
            "reply": reply,
            "code": code,
            "autotest": res.get("autotest") or {},
            "followup": res.get("followup") or {},
        }

    # -- actions ----------------------------------------------------------
    def run(self, args="", confirm=False):
        if not self.code:
            return {"error": "nothing built yet"}
        r = core.run_code(self.code, args, confirm, self.name)
        if "needsConfirm" not in r:
            core.log_run(self.name, args, r)
            self.last_run = r
        return r

    def selftest(self):
        if not self.code:
            return {"error": "nothing built yet"}
        return core.probe_run(self.code, self.name)

    def review(self):
        if not self.code:
            return {"error": "nothing built yet"}
        return core.review_code(self.code)

    def fix_from_log(self):
        if not self.code:
            return {"error": "nothing built yet"}
        convo = [self._system()] + self.messages
        r = core.fix_from_log(self.code, convo)
        if r.get("error"):
            return r
        code = core.extract_code(r.get("reply", ""))
        if code:
            self.code = code
            self._bump()
        self.messages.append({"role": "assistant", "content": r.get("reply", "")})
        return {"reply": r.get("reply", ""), "code": code}

    def deps(self, install=False):
        if not self.code:
            return {"error": "nothing built yet"}
        d = core.detect_deps(self.code)
        if not install:
            return d
        pkgs = d.get("pip") or []
        if not pkgs:
            return {**d, "log": "pure stdlib — nothing to install", "ok": True}
        return {**d, **core.install_deps(pkgs)}

    def save(self):
        if not self.code:
            return {"error": "nothing built yet"}
        return core.save_tool(self.code, self.name, self.stage)

    def build(self):
        if not self.code:
            return {"error": "nothing built yet"}
        # console=True: these are terminal tools, they must keep their stdio
        return core.build_executable(self.code, self.name, console=True)

    def reset(self):
        self.__init__(gui=self.gui)


# ==========================================================================
# OUTPUT  --  the one thing the two front ends implement differently.
# Every command below writes through this interface, so the TUI and the REPL
# render the same content and can't drift.
# ==========================================================================
class Out:
    def info(self, m): raise NotImplementedError
    def ok(self, m): raise NotImplementedError
    def warn(self, m): raise NotImplementedError
    def err(self, m): raise NotImplementedError
    def dim(self, m): raise NotImplementedError
    def say(self, m): raise NotImplementedError      # the model talking
    def code(self, c): raise NotImplementedError
    def rule(self, t=""): raise NotImplementedError
    def busy(self, m): self.dim(m)


C = {
    "reset": "\033[0m", "dim": "\033[2m", "bold": "\033[1m",
    "amber": "\033[38;5;179m", "lime": "\033[38;5;149m", "red": "\033[38;5;167m",
    "cyan": "\033[38;5;80m", "grey": "\033[38;5;245m", "mag": "\033[38;5;176m",
}


def _colour_ok():
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


class TermOut(Out):
    """Plain ANSI output. Degrades to bare text when piped or NO_COLOR is set —
    the same rule CLI Dawg teaches the model to follow."""

    def __init__(self):
        self.c = C if _colour_ok() else {k: "" for k in C}

    def _p(self, colour, m):
        print(f"{self.c[colour]}{m}{self.c['reset']}")

    def info(self, m): self._p("cyan", m)
    def ok(self, m): self._p("lime", m)
    def warn(self, m): self._p("amber", m)
    def err(self, m): self._p("red", m)
    def dim(self, m): self._p("grey", m)

    def say(self, m):
        print(f"{self.c['amber']}dawg{self.c['reset']}  {m}\n")

    def code(self, c):
        n = len(c.splitlines())
        self.dim(f"  ── {n} lines ──")
        try:
            from rich.console import Console
            from rich.syntax import Syntax
            Console().print(Syntax(c, "python", theme="ansi_dark",
                                   line_numbers=True, word_wrap=False))
        except Exception:
            for i, line in enumerate(c.splitlines(), 1):
                print(f"{self.c['grey']}{i:>4}{self.c['reset']}  {line}")

    def rule(self, t=""):
        w = min(shutil_width(), 78)
        bar = "─" * max(4, w - len(t) - 3)
        print(f"{self.c['grey']}── {t} {bar}{self.c['reset']}" if t
              else f"{self.c['grey']}{'─' * w}{self.c['reset']}")


def shutil_width():
    import shutil
    return shutil.get_terminal_size((80, 24)).columns


# ==========================================================================
# COMMANDS  --  shared by both front ends
# ==========================================================================
HELP = """
  talk to it              just type what you want built
  /run [args]             run the tool (add args after the command)
  /stop                   stop a running tool
  /test                   self-test: run it and report what happened
  /fix                    send the last run's output back for a fix
  /review                 static + AI review of the current code
  /deps [install]         show third-party imports, or install them
  /code                   print the current source
  /save                   write the tool to ~/thedawg-tools
  /build                  package it into a single-file executable
  /name <n>               rename the tool
  /gui                    switch to building GUI tools (default is CLI)
  /cli                    switch back to building command-line tools
  /cost                   tokens used this session and roughly what they cost
  /model                  show provider and model
  /new                    start a fresh tool
  /doctor                 check this machine
  /help  ·  /quit
"""


def _fmt_autotest(at, out):
    """Report what the silent test loop did, briefly."""
    if not at or not at.get("ran"):
        return
    rounds = at.get("rounds") or []
    if at.get("passed"):
        if len(rounds) > 1:
            out.dim(f"  auto-test: passed after {len(rounds)} rounds")
        else:
            out.dim("  auto-test: passed")
    else:
        out.warn(f"  auto-test: still failing after {len(rounds)} rounds")
        last = rounds[-1] if rounds else {}
        if last.get("report"):
            out.dim("  " + last["report"].splitlines()[0][:100])


def _fmt_run(r, out):
    if r.get("error"):
        out.err("  " + r["error"]); return
    if r.get("needsConfirm"):
        out.warn("  this script matches a destructive pattern:")
        for p in r.get("patterns", []):
            out.warn(f"    {p}")
        out.warn("  run  /run! [args]  to go ahead anyway")
        return
    # `gui: True` is set on the failure path too — a tool that died on startup
    # was cheerfully announced as "launched (pid None)" right before its exit 1.
    if r.get("launched") and r.get("pid"):
        out.ok(f"  launched (pid {r['pid']})  —  /stop to kill it")
    # run_code's key is "exit" — reading "rc" here silently reported nothing,
    # which for a tool that preaches honest exit codes was embarrassing.
    rc = r.get("exit")
    if rc is not None:
        (out.ok if rc == 0 else out.err)(f"  exit {rc}")
    for stream, style in (("stdout", out.dim), ("stderr", out.err)):
        blob = (r.get(stream) or "").rstrip()
        if blob:
            out.rule(stream)
            for line in blob.splitlines()[-40:]:
                style("  " + line)


def handle_command(sess, line, out):
    """Run a /command. Returns "quit" to exit, True if handled, False if not a command."""
    if not line.startswith("/"):
        return False
    parts = line[1:].split(None, 1)
    cmd = parts[0].lower() if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("quit", "q", "exit"):
        return "quit"

    if cmd in ("help", "h", "?"):
        out.dim(HELP); return True

    if cmd == "code":
        if not sess.code: out.warn("  nothing built yet")
        else: out.code(sess.code)
        return True

    if cmd in ("run", "run!"):
        out.busy("  running…")
        _fmt_run(sess.run(rest, confirm=(cmd == "run!")), out)
        return True

    if cmd == "stop":
        # list_running() hands back {"running": [...]}. Iterating the dict gave
        # the key string and then crashed on p["pid"]; a non-empty dict is also
        # always truthy, so the "nothing running" branch could never fire.
        running = (core.list_running() or {}).get("running") or []
        if not running:
            out.dim("  nothing running"); return True
        for p in running:
            res = core.stop_running(p["pid"])
            if res.get("ok"):
                out.ok(f"  stopped {p.get('name', 'tool')} (pid {p['pid']})")
            else:
                out.warn(f"  pid {p['pid']}: {res.get('error', 'could not stop')}")
        return True

    if cmd == "test":
        if not sess.code: out.warn("  nothing built yet"); return True
        out.busy("  self-testing…")
        p = sess.selftest()
        out.dim("  " + (core.render_probe_report(p) or "no report").replace("\n", "\n  "))
        return True

    if cmd == "fix":
        if not sess.code: out.warn("  nothing built yet"); return True
        out.busy("  reading the log and fixing…")
        r = sess.fix_from_log()
        if r.get("error"): out.err("  " + r["error"]); return True
        out.say(_prose(r.get("reply", "")))
        if r.get("code"): out.ok(f"  patched → v{sess.ver}")
        return True

    if cmd == "review":
        if not sess.code: out.warn("  nothing built yet"); return True
        out.busy("  reviewing…")
        r = sess.review()
        if r.get("error"): out.err("  " + r["error"]); return True
        out.info("  " + (r.get("verdict") or ""))
        for i in (r.get("issues") or [])[:8]:
            sev = (i.get("severity") or "low").lower()
            style = {"high": out.err, "medium": out.warn}.get(sev, out.dim)
            ln = f" (line {i['line']})" if i.get("line") else ""
            style(f"  [{sev}] {i.get('title','')}{ln}")
            if i.get("detail"):
                out.dim(f"        {i['detail'][:160]}")
        if not (r.get("issues") or []):
            out.ok("  no issues found")
        return True

    if cmd == "deps":
        want_install = rest.lower().startswith("i")
        out.busy("  installing…" if want_install else "  checking…")
        d = sess.deps(install=want_install)
        if d.get("error"): out.err("  " + d["error"]); return True
        pip = d.get("pip") or []
        out.dim(f"  third-party: {', '.join(pip) if pip else 'none — pure stdlib'}")
        tk = d.get("toolkit")
        if tk and tk.get("sys_hint"):
            out.dim(f"  {tk['label']} comes from your distro: {tk['sys_hint']}")
        if want_install and (d.get("log") or "").strip():
            # .splitlines() on a whitespace-only log is [], and [-1] raised
            tail = d["log"].strip().splitlines()
            out.dim("  " + (tail[-1][:120] if tail else "done"))
            (out.ok if d.get("ok") else out.err)("  " + ("installed" if d.get("ok") else "install failed"))
        elif pip:
            out.dim("  /deps install  to put them in the managed venv")
        return True

    if cmd == "save":
        r = sess.save()
        if r.get("error"): out.err("  " + r["error"])
        else: out.ok(f"  saved → {r.get('path')}")
        return True

    if cmd == "build":
        if not sess.code: out.warn("  nothing built yet"); return True
        out.busy("  packaging with PyInstaller (this takes a minute)…")
        r = sess.build()
        if r.get("ok"): out.ok(f"  built → {r.get('path')}")
        else:
            out.err("  build failed")
            out.dim("  " + (r.get("log") or "")[-400:])
        return True

    if cmd == "name":
        if rest:
            sess.name = rest.replace(" ", "_")
            out.ok(f"  now called {sess.filename}")
        else:
            out.dim(f"  {sess.filename}  v{sess.ver}  ({sess.stage})")
        return True

    if cmd in ("gui", "cli"):
        sess.gui = (cmd == "gui")
        out.ok(f"  building {'GUI apps' if sess.gui else 'command-line tools'} from here")
        return True

    if cmd == "cost":
        u = core.usage_summary()
        t = u["session"]
        if not t["calls"]:
            out.dim("  nothing spent yet"); return True
        out.dim(f"  {t['calls']} calls · {t['in']:,} in / {t['out']:,} out "
                f"= {t['in'] + t['out']:,} tokens")
        for m, v in sorted(u["by_model"].items(), key=lambda kv: -kv[1]["in"]):
            short = m.split("/")[-1]
            out.dim(f"    {short:26} {v['calls']:>3} calls  {v['in'] + v['out']:>9,} tok")
        if u["cost_usd"] > 0:
            approx = "" if u["cost_complete"] else " (partial — some models unpriced)"
            out.info(f"  approx ${u['cost_usd']:.4f}{approx}")
        return True

    if cmd == "model":
        pid = core.STATE["provider"]
        chain = core.provider_model_chain(pid)
        model = core.STATE["models"].get(pid) or (chain[0] if chain else "?")
        out.dim(f"  {core.PROVIDERS[pid]['label']}  ·  {model}")
        out.dim(f"  host: {core.DISTRO.get('pretty')}  ·  {core.DISTRO.get('install')}")
        return True

    if cmd == "new":
        sess.reset()
        out.ok("  fresh tool — what are we building?")
        return True

    if cmd == "doctor":
        core.doctor()
        return True

    out.warn(f"  unknown command: /{cmd}   (/help)")
    return True


def _prose(reply):
    """The model's message minus its code block — the code is shown separately."""
    import re
    return re.sub(r"```[\s\S]*?```", "", reply or "").strip() or "(code updated)"


# ==========================================================================
# KEYS
# ==========================================================================
def ensure_key(out):
    """CLI Dawg shares TheDawg's config, so a key set in either works in both."""
    have = [p for p in core.PROVIDERS if core.STATE["keys"].get(p)]
    if have:
        return True
    out.warn("  no API key yet.")
    out.dim("  set one in the environment, then rerun:")
    for pid, p in core.PROVIDERS.items():
        out.dim(f"    export {p['env']}=...        # {p['label']}")
    out.dim("  or paste one now (blank to skip):")
    try:
        key = input("  key> ").strip()
    except (EOFError, KeyboardInterrupt):
        return False
    if not key:
        return False
    pid = ("groq" if key.startswith("gsk_") else
           "google" if key.startswith("AIza") else
           "novita" if key.startswith("sk_") else "siliconflow")
    core.STATE["keys"][pid] = key
    core.STATE["provider"] = pid
    core.persist_state()
    out.ok(f"  saved for {core.PROVIDERS[pid]['label']}")
    return True


BANNER = r"""
   ___ _    ___   ___                
  / __| |  |_ _| |   \ __ ___ __ ___ 
 | (__| |__ | |  | |) / _` \ V  V / _`|
  \___|____|___| |___/\__,_|\_/\_/\__, |
                                   |___/
"""


# ==========================================================================
# FRONT END 1 — PLAIN REPL
# Deliberately dumb: one line in, one block out. Works over ssh, over serial,
# inside tmux with a broken TERM, and anywhere Textual won't start.
# ==========================================================================
def run_repl(sess):
    out = TermOut()
    c = out.c
    print(f"{c['amber']}{BANNER}{c['reset']}")
    out.dim(f"  cli dawg {__version__}  ·  engine {core.__version__}  ·  "
            f"{core.DISTRO.get('pretty')}")
    out.dim("  describe a tool, or /help for commands\n")
    if not ensure_key(out):
        out.err("  no key — can't build without one."); return 1

    try:
        import readline  # noqa: F401  — arrow keys and history for free
        hist = Path(core.config_dir()) / "cli_history"
        hist.parent.mkdir(parents=True, exist_ok=True)
        try:
            readline.read_history_file(hist)
        except Exception:
            pass
    except Exception:
        hist = None

    while True:
        try:
            line = input(f"{c['cyan']}you{c['reset']}  ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        r = handle_command(sess, line, out)
        if r == "quit":
            break
        if r:
            continue

        out.busy("  forging…")
        t0 = time.time()
        try:
            res = sess.send(line)
        except KeyboardInterrupt:
            # Ctrl-C while the model is thinking should abandon THAT TURN, not
            # throw away the session and everything built in it.
            print()
            out.warn("  cancelled — the tool and the conversation are intact")
            if sess.messages and sess.messages[-1].get("role") == "user":
                sess.messages.pop()
            continue
        if res.get("error"):
            out.err("  " + res["error"]); continue
        out.say(_prose(res["reply"]))
        if res.get("code"):
            out.ok(f"  {sess.filename}  v{sess.ver}   ({len(res['code'].splitlines())} lines, "
                   f"{time.time() - t0:.0f}s)")
            _fmt_autotest(res.get("autotest"), out)
            u = core.usage_summary()["session"]
            out.dim(f"  {u['in'] + u['out']:,} tokens this session over {u['calls']} calls "
                    f"(/cost for the breakdown)")
            out.dim("  /code to read it · /run to run it · /save to keep it")
        for q in (res.get("followup") or {}).get("questions", [])[:4]:
            out.info("  ? " + q.get("q", ""))
            opts = q.get("options") or []
            if opts:
                out.dim("    " + "  |  ".join(opts))

    if hist:
        try:
            import readline
            readline.write_history_file(hist)
        except Exception:
            pass
    out.dim("\n  forge banked. later, dawg.\n")
    return 0


# ==========================================================================
# FRONT END 2 — ONE-SHOT
# For scripts and muscle memory: build a thing, save it, print the path,
# exit with an honest code. No dialogue.
# ==========================================================================
def run_once(sess, request, do_run=False, do_save=True, quiet=False):
    out = TermOut()
    if not core.STATE["keys"] or not any(core.STATE["keys"].values()):
        out.err("no API key set — see `clidawg --help`")
        return 1
    if not quiet:
        out.dim(f"  forging: {request}")
    res = sess.send(request)
    if res.get("error"):
        out.err(res["error"]); return 1
    if not res.get("code"):
        # it asked a question instead of building — show it and say so
        out.warn(_prose(res["reply"]))
        out.dim("  (it needs more detail — try `clidawg` for the full dialogue)")
        return 2
    if not quiet:
        _fmt_autotest(res.get("autotest"), out)
    saved = sess.save() if do_save else {}
    if saved.get("path"):
        print(saved["path"])            # stdout is data: the path, and only the path
    if do_run:
        r = sess.run("")
        _fmt_run(r, out)
        # same key bug: this made `clidawg build ... --run` exit 0 even when the
        # tool it just built had failed, which lies to && and to CI.
        rc = r.get("exit")
        return 0 if rc in (0, None) else 1
    at = res.get("autotest") or {}
    return 0 if (not at.get("ran") or at.get("passed")) else 1


# ==========================================================================
# FRONT END 3 — TEXTUAL TUI
# The full workspace: dialogue on the left, live source on the right, run output
# underneath. Every model call and every subprocess runs on a worker thread, so
# the interface never locks up mid-forge.
# ==========================================================================
TUI_CSS = """
Screen { background: $surface; }
#body { height: 1fr; }
#chatpane { width: 45%; border-right: solid $panel-lighten-2; }
#codepane { width: 1fr; }
#chat, #code, #console { background: transparent; padding: 0 1; }
#code { height: 1fr; }
#console { height: 30%; border-top: solid $panel-lighten-2; }
.paneltitle {
    background: $panel; color: $text-muted; text-style: bold;
    padding: 0 1; height: 1;
}
#prompt { border: tall $primary; background: $panel; }
#prompt:focus { border: tall $accent; }
#status { height: 1; background: $panel; color: $text-muted; padding: 0 1; }
"""


def make_tui_app(sess):
    """Build the Textual app for this session.

    Split out from run_tui so the interface can be driven headlessly by
    Textual's Pilot in tests — a TUI you can't drive is a TUI you're guessing at.
    """
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import Footer, Header, Input, RichLog, Static
        from textual import work
        from rich.syntax import Syntax
        from rich.text import Text
    except Exception as e:
        print(f"cli dawg: Textual isn't available ({e}).\n"
              f"  install it:  pip install textual\n"
              f"  or run the plain REPL:  clidawg --plain", file=sys.stderr)
        return 1

    class LogOut(Out):
        """Writes everything the shared commands emit into the run-output pane.

        The same commands run from two places: worker threads (a build turn) and
        the app thread itself (on_mount, a key binding). Textual's
        call_from_thread REFUSES to run on the app thread — it raises rather than
        falling back — so this dispatches on where it's actually being called
        from instead of assuming.
        """
        def __init__(self, app, widget):
            self.app, self.w = app, widget

        def _call(self, fn, *args):
            if threading.get_ident() == getattr(self.app, "_ui_thread", None):
                fn(*args)                       # already on the app thread
            else:
                self.app.call_from_thread(fn, *args)

        def _w(self, msg, style):
            self._call(self.w.write, Text(str(msg), style=style))

        def info(self, m): self._w(m, "cyan")
        def ok(self, m): self._w(m, "green")
        def warn(self, m): self._w(m, "yellow")
        def err(self, m): self._w(m, "red")
        def dim(self, m): self._w(m, "dim")
        def say(self, m): self._call(self.app.add_dawg, str(m))
        def code(self, c): self._call(self.app.show_code, c)
        def rule(self, t=""): self._w(f"── {t} " + "─" * max(2, 40 - len(t)), "dim")

    class CliDawg(App):
        CSS = TUI_CSS
        TITLE = "CLI Dawg"
        BINDINGS = [
            ("ctrl+q", "quit", "quit"),
            ("ctrl+r", "run_tool", "run"),
            ("ctrl+s", "save_tool", "save"),
            ("ctrl+l", "clear_console", "clear log"),
            ("f1", "help", "help"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=False)
            with Horizontal(id="body"):
                with Vertical(id="chatpane"):
                    yield Static("build dialogue", classes="paneltitle")
                    yield RichLog(id="chat", wrap=True, markup=False, highlight=False)
                with Vertical(id="codepane"):
                    yield Static("no draft yet", classes="paneltitle", id="codetitle")
                    yield RichLog(id="code", wrap=False, markup=False, highlight=False)
                    yield Static("run output", classes="paneltitle")
                    yield RichLog(id="console", wrap=True, markup=False, highlight=False)
            yield Static("", id="status")
            yield Input(placeholder="describe a tool, or /help …", id="prompt")
            yield Footer()

        # -- lifecycle ----------------------------------------------------
        def on_mount(self):
            # remember which thread owns the UI, so LogOut knows whether it needs
            # call_from_thread or can write directly
            self._ui_thread = threading.get_ident()
            self.chat = self.query_one("#chat", RichLog)
            self.codelog = self.query_one("#code", RichLog)
            # NB: do NOT call this `self.console` — App.console is Textual's own
            # Rich Console, and shadowing it breaks the framework from the inside.
            self.runlog = self.query_one("#console", RichLog)
            self.out = LogOut(self, self.runlog)
            self.busy = False
            self.refresh_status()
            self.query_one("#prompt", Input).focus()
            self.add_dawg("Right — what do you want built? Describe the job and I'll "
                          "forge a command-line tool for it. /help for commands.")
            if not any(core.STATE["keys"].values()):
                self.out.err("no API key set. Set one and restart:")
                for pid, p in core.PROVIDERS.items():
                    self.out.dim(f"  export {p['env']}=…   # {p['label']}")

        def refresh_status(self):
            pid = core.STATE["provider"]
            chain = core.provider_model_chain(pid)
            model = core.STATE["models"].get(pid) or (chain[0] if chain else "?")
            kind = "GUI apps" if sess.gui else "CLI tools"
            u = core.usage_summary()
            tot = u["session"]["in"] + u["session"]["out"]
            spend = (f" │ {tot / 1000:.1f}k tok"
                     + (f" ~${u['cost_usd']:.3f}" if u["cost_usd"] > 0 else "")) if tot else ""
            self.query_one("#status", Static).update(
                f" {sess.name}.py  v{sess.ver} · {sess.stage} │ building {kind} │ "
                f"{core.PROVIDERS[pid]['label']} {model}{spend}"
                + ("  │ working…" if self.busy else ""))

        # -- rendering helpers --------------------------------------------
        def add_you(self, text):
            self.chat.write(Text("you", style="bold cyan"))
            self.chat.write(Text(text + "\n"))

        def add_dawg(self, text):
            self.chat.write(Text("dawg", style="bold yellow"))
            self.chat.write(Text(text + "\n"))

        def show_code(self, code):
            self.codelog.clear()
            self.codelog.write(Syntax(code, "python", theme="ansi_dark",
                                      line_numbers=True, word_wrap=False))
            self.query_one("#codetitle", Static).update(
                f"{sess.filename}  v{sess.ver}  ·  {len(code.splitlines())} lines")

        def set_busy(self, flag):
            self.busy = flag
            self.query_one("#prompt", Input).disabled = flag
            self.refresh_status()

        # -- input --------------------------------------------------------
        def on_input_submitted(self, event: Input.Submitted):
            text = event.value.strip()
            if not text or self.busy:
                return
            event.input.value = ""
            if text.startswith("/"):
                # "/" on its own has no command word — split()[0] raised IndexError
                # and killed the input handler.
                word = (text[1:].split() or [""])[0].lower()
                if word in ("quit", "q", "exit"):
                    self.exit(); return
                self.set_busy(True)
                self.do_command(text)
            else:
                self.add_you(text)
                self.set_busy(True)
                self.do_send(text)

        # -- workers: every blocking call lives off the UI thread ----------
        @work(thread=True, exclusive=True)
        def do_send(self, text):
            t0 = time.time()
            res = sess.send(text)
            if res.get("error"):
                self.out.err("  " + res["error"])
            else:
                self.out.say(_prose(res["reply"]))
                if res.get("code"):
                    self.out.code(res["code"])
                    self.out.ok(f"  {sess.filename} v{sess.ver} "
                                f"({len(res['code'].splitlines())} lines, {time.time()-t0:.0f}s)")
                    _fmt_autotest(res.get("autotest"), self.out)
                for q in (res.get("followup") or {}).get("questions", [])[:4]:
                    self.out.say("? " + q.get("q", ""))
                    opts = q.get("options") or []
                    if opts:
                        self.out.dim("    " + "  |  ".join(opts))
            self.call_from_thread(self.set_busy, False)

        @work(thread=True, exclusive=True)
        def do_command(self, text):
            try:
                handle_command(sess, text, self.out)
            except Exception as e:
                self.out.err(f"  {type(e).__name__}: {e}")
            if sess.code:
                self.call_from_thread(self.show_code, sess.code)
            self.call_from_thread(self.set_busy, False)

        # -- key actions ---------------------------------------------------
        def action_run_tool(self):
            if not self.busy:
                self.set_busy(True); self.do_command("/run")

        def action_save_tool(self):
            if not self.busy:
                self.set_busy(True); self.do_command("/save")

        def action_clear_console(self):
            self.runlog.clear()

        def action_help(self):
            self.out.dim(HELP)

    return CliDawg()


def run_tui(sess):
    app = make_tui_app(sess)
    if isinstance(app, int):          # import failed; it returned the exit code
        return app
    app.run()
    return 0


# ==========================================================================
# ENTRY
# ==========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="clidawg",
        description="CLI Dawg — an AI toolsmith for the terminal. Builds "
                    "command-line tools, runs them, and fixes them.",
        epilog=textwrap.dedent("""\
            examples:
              clidawg                                 full TUI
              clidawg --plain                         line-by-line REPL
              clidawg build "a subnet ping sweeper"   one-shot, prints the saved path
              clidawg build "a csv column summariser" --run
              clidawg --doctor
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=["build"],
                    help="build: one-shot mode (non-interactive)")
    ap.add_argument("request", nargs="?", help="what to build, in plain English")
    ap.add_argument("--plain", action="store_true",
                    help="line-by-line REPL instead of the TUI")
    ap.add_argument("--gui", action="store_true",
                    help="build GUI apps instead of command-line tools")
    ap.add_argument("--run", action="store_true",
                    help="one-shot: run the tool after building it")
    ap.add_argument("--no-save", action="store_true",
                    help="one-shot: don't write the tool to disk")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="one-shot: only print the resulting path")
    ap.add_argument("--doctor", action="store_true", help="check this machine")
    ap.add_argument("--version", action="version",
                    version=f"cli dawg {__version__} (engine {core.__version__})")
    args = ap.parse_args(argv)

    if args.doctor:
        core.doctor()
        return 0

    sess = Session(gui=args.gui)

    if args.command == "build":
        if not args.request:
            ap.error("build needs a description, e.g. clidawg build \"a port scanner\"")
        return run_once(sess, args.request, do_run=args.run,
                        do_save=not args.no_save, quiet=args.quiet)

    # interactive: TUI unless asked otherwise, or unless there's no real terminal
    if args.plain or not sys.stdout.isatty():
        return run_repl(sess)
    try:
        return run_tui(sess)
    except Exception as e:
        print(f"cli dawg: TUI failed to start ({e}) — falling back to plain mode\n",
              file=sys.stderr)
        return run_repl(sess)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
