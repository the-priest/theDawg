# TheDawg 2.5.6 — read-through pass: 13 bugs the tests didn't catch

No test harness here — this is a straight read of `thedawg.py`, `ui/index.html`,
`clidawg.py` and `shell.py`. Every finding below is stated as what breaks, not as
what looks untidy. Where a bug is mechanically checkable I checked it against
2.5.4 as well, and those runs are quoted.

---

## 1. One comma turned a GUI tool into a 120-second hang

`_IMPORT_RE` captured exactly ONE module name per import statement:

```python
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", re.M)
```

So on `import sys, tkinter as tk` it saw `sys`. Nothing else.

That is not a cosmetic miss, because `detect_toolkit()` is what `run_code()`
branches on:

```
2.5.4:  detect_toolkit("import sys, tkinter as tk") -> None
```

`None` means "not a GUI", which sends the tool down the **non-GUI path** — run
with captured output and a 120-second timeout, no window, no launch. The user
pressed ▶ launch on a perfectly good Tkinter tool, waited two minutes, and got:

> Killed: exceeded 120s (possible infinite loop, or the tool was waiting for input)

The same miss dropped the second package from ⬇ deps (`import requests, bs4`
installed only `requests`) and from the PyInstaller build's dependency list.
Fixed with `_imported_tops()`, which parses the whole import line.

## 2. CLI Dawg's `/fix` told the model to build a GUI

`fix_from_log()` stripped every system message out of the caller's conversation
and hardcoded TheDawg's own:

```python
convo = [m for m in messages if m.get("role") != "system"]
convo = [{"role": "system", "content": SYSTEM_PROMPT}] + convo + [...]
```

`clidawg.py` shares this engine and passes its own CLI doctrine. So `/fix` on a
command-line tool handed the model *"Every tool you produce opens a real window —
never a bare command-line script"*, plus a prompt describing "a runtime probe that
actually opened the window", and asked it to fix a terminal program.

```
2.5.4:  system prompts seen -> ['You are TheDawg, a senior ...']   CLI doctrine: lost
2.5.6:  system prompts seen -> ['You are CLI Dawg. Never bu...']   CLI doctrine: kept
```

New `_caller_system()`; applied to `fix_from_log`, `polish_round` and
`_autotest_existing`'s rewrite fallback.

## 3. Attaching a README corrupted the message

`send()` wrapped every attached reference file in a hardcoded three-backtick
fence — in a file that already has `fenceFor()` for exactly this reason, used
correctly in `loadCodeFile()` since 2.2.3 and missed here. Attach any markdown
doc, spec or log containing a fenced example and its own fence closed ours:

```
attachment as the server parses it: '# Notes\n\n```bash\nls -la\n'   <- cut here
```

The rest became loose prose and the stray closer opened a phantom block that
`extract_code` could pick up instead of the tool.

## 4. ＋ new tool during a build wrote the old tool into the new session

`converse()` writes `loadCode(code)`, `state.messages.push(...)` and an autosave
*after* its await. Nothing checked whether the workspace was still the one the
build started in — and `＋ new tool`, `resume session` and `open from library`
all swap the workspace without touching `state.busy`. Start a build, hit ＋ new
tool, and the reply landed in the fresh session: old code in the pane, old
assistant turn in the conversation, and the mixture autosaved as a new record.

Added a workspace generation token; a build whose workspace has been swapped is
discarded with one toast instead of written.

## 5. The GitHub release flow never claimed busy — and lost the question

Two bugs in one handler. It runs a full release generation (minutes) while
leaving the composer live, so a message sent mid-release interleaved two writers
on one conversation — the exact bug the polish loop was fixed for in 2.2.3, still
open here. And on success it pushed only the assistant reply:

```js
state.messages.push({role:"assistant", content:rel.reply});
```

The instruction that produced it was never recorded, so the saved conversation
ends with a polished file answering nothing, two assistant turns can end up
adjacent, and the next build sees a release rewrite it never asked for. Same
omission in `/fix` (`applyModelResult`) and in every polish round. All three now
record the request.

## 6. Doing as it was told disabled targeted edits

`EDIT_PROMPT` ends with:

> If the request genuinely requires rewriting most of the file, reply with the
> single word FULL_REWRITE and nothing else.

`grep -rn FULL_REWRITE` finds exactly one hit: that sentence. Nothing ever
checked for it. A model that complied produced no edit blocks and no salvageable
script, so it was scored as a failed patch — and three failures in a row make
`_edit_lost()` switch targeted editing **off for the rest of the process**,
silently putting every later change back on the expensive full-rewrite path.

```
2.5.6:  4x FULL_REWRITE -> edit mode still armed: True, streak 0
```

## 7. ★ library silently destroyed a saved tool

The record id was `_safe_id(name)`, and the name is auto-derived from the tool's
own window title. Two different tools that title themselves the same thing landed
on one filename — the second save destroyed the first, no warning, no undo, and
the library showed one entry where two were saved.

```
2.5.4:  save "converter"(A), save "converter"(B) -> ids ['converter']              bodies ['B']
2.5.6:  same two saves                           -> ids ['converter','converter-2'] both kept
```

Re-saving the *same* tool still overwrites — matched on the originating session.

## 8. ↻ refresh models silently re-pinned your model

```js
if(p && r.models){ p.models=r.models; p.chosen=r.models[0]; ... }
```

`p.chosen` is the model the user pinned. Refreshing the catalog overwrote it with
whatever ranks first, the dropdown moved to that, and Save posted it as the new
pin. Refreshing a list is not a request to change the choice. Now kept whenever
it's still on offer. (Same line in the save-key path.)

## 9. Nine more, briefly

- **Empty key wiped the running key.** `POST /api/key` with a blank key cleared
  `STATE["keys"][pid]` and then skipped the save — the app went "no key" while the
  config file still held a good one, a state only a restart explained. Blank now
  means "leave it alone", which is what the Settings placeholder already promises.
- **Concurrent probes fought over one screenshot.** `SHOT_PATH` and `LAST_PROBE`
  are single globals and `probe_run` deletes the image on entry. The polish loop
  fires a probe per round while 🔎 self-test stays clickable, so one probe wiped
  the other's image (broken image in the panel) and overwrote the evidence that
  "send log to AI" reads. Serialised.
- **⤓ log was still dead in the native window.** 2.2.3 swapped `window.open()` for
  a Blob download — a *browser* fix. WebKitGTK cancels any download whose
  destination nothing sets, and `shell.py` connected no `download-started`
  handler. Added one; downloads land in ~/Downloads with a de-duplicated name.
- **Retry paths hadn't caught up with 2.5.5.** The 401 regional-host retry and the
  transient same-model retry each kept their own `content or reasoning_content`
  line, so a truncated response returning through a *retry* still surfaced as a
  raw reasoning monologue with no `truncated` flag. Shared `_unpack_reply()` now.

## Verified

`thedawg.py`, `clidawg.py`, `shell.py` compile; `ui/index.html` JS syntax-checked;
`--doctor` clean; the 2.5.5 transport tests still pass (60s streamed build end to
end through the real handler, code delivered, 8 live progress frames; truncated
build recovers; gateway that rejects `stream` falls back).

| check | 2.5.4 | 2.5.6 |
|---|---|---|
| `import requests, bs4` → deps | `['requests']` | `['bs4','requests']` |
| `import sys, tkinter as tk` → toolkit | `None` | Tkinter |
| two same-named library saves | 1 record, one destroyed | 2 records |
| CLI Dawg `/fix` doctrine | GUI prompt injected | CLI prompt kept |
| 4× FULL_REWRITE | edit mode disabled | still armed |

## Not changed

No feature, API or file-format changes. `reasoning_effort` still defaults to
`high`. `install.sh` and the assets are untouched.

## Known, still open by choice

`trim_history` stage 2 can still separate an assistant reply from the user turn
it answered — that needs turn-pair trimming, which is a restructure rather than a
patch. Unchanged since 2.2.2 and noted here so it doesn't look like an oversight.
