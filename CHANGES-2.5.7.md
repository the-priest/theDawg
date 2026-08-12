# TheDawg 2.5.7 — "sometimes it tells me to refresh model"

Two things in this one: the intermittent error, and the refusals.

---

## Part 1 — the intermittent error, and why it was intermittent

A 502 or 503 from a busy gateway is a routine, self-healing event. TheDawg had
**no retry for it at all.**

And because you pin a model in Settings, `chain = [canon]` — the model chain was
exactly one model long. So one hiccup exhausted the whole chain, and with no
second provider key configured there was nothing to fall through to. The result
was a hard error, in **zero seconds**, ending with advice for an unrelated
problem:

```
2.5.4/2.5.6, pinned model, no fallback key:

  503        0.0s   "chain failed ... Try Settings -> refresh models"
  502        0.0s   "chain failed ... Try Settings -> refresh models"
  500        0.0s   "chain failed ... Try Settings -> refresh models"
  empty      0.0s   "chain failed ... Try Settings -> refresh models"
  no choices 0.0s   "chain failed ... Try Settings -> refresh models"
  conn reset 2.1s   "chain failed ... Try Settings -> refresh models"
```

That's the whole mystery. Nothing was wrong with your setup, your key or your
model list — the provider had a bad second and TheDawg gave up on the first one,
then told you to go fix a model catalog that was perfectly fine.

### The fix

**A real retry policy.** `_post()` now classifies each failure and backs off:

- **5xx, 408, 409, 425, 429, 52x** — retried up to `THEDAWG_RETRIES` (default 4)
  with exponential backoff and jitter, honouring `Retry-After` when the server
  sends one (capped at 20s; longer than that and we move on instead of freezing).
- **Dropped connection, reset, incomplete read** — retried. This request is
  definitively gone, so re-sending is exactly right.
- **Read timeout** — *not* retried on the same model. The provider may still be
  generating, and a re-send buys you a second full generation on the bill. This
  is the one case where moving on is cheaper than trying again.
- **Empty reply / empty choices** — retried. Previously fatal on first occurrence.

**A pinned model no longer dead-ends.** `chain = [canon]` is now
`[canon] + siblings`. The original reason for pinning the chain to one model was
sound — one blip shouldn't silently drop you onto Qwen — but with no retry
underneath it, one blip *was* the entire chain. Now a sibling is only ever
reached after every retry on your model is spent, and it says so on screen when
it happens:

> deepseek-ai/DeepSeek-V4-Pro isn't answering after 4 tries — falling back to
> deepseek-ai/DeepSeek-V4-Flash so the build still happens

**Provider-degraded detection.** Once one model burns its full budget on 5xx, the
*provider* is having the problem, not the model — so the rest of the chain gets
one attempt each rather than four, and we reach the fallback provider quickly
instead of making you wait 4× for the same answer.

**The error message now matches the cause.** "Try Settings → refresh models" is
only suggested when the failure is genuinely about the model catalog (a 404 /
model-not-available). A 5xx says the provider is having a moment and that
TheDawg already retried; a 429 explains the rate limit; a timeout points at
`THEDAWG_IDLE_TIMEOUT` and `THEDAWG_REASONING=medium`.

### Measured

Same faults, 2.5.7, pinned model, no fallback key:

| fault | recovered | model used | time |
|---|---|---|---|
| 503 ×1 | **yes** | your pinned V4-Pro | 0.8s |
| 503 ×2 | **yes** | your pinned V4-Pro | 2.7s |
| 502 ×3 | **yes** | your pinned V4-Pro | 5.1s |
| connection reset ×2 | **yes** | your pinned V4-Pro | 2.9s |
| empty reply ×2 | **yes** | your pinned V4-Pro | 2.5s |
| 503 ×9 (provider really down) | no | — | 15.8s, accurate message |

New lever: `THEDAWG_RETRIES=4` (0–10).

---

## Part 2 — the refusals

There are two different things refusing you, and they need different answers.

### TheDawg's own gate — fixed properly

`looks_dangerous()` matched patterns against the **raw file, including comments
and docstrings**. So a tool that merely *talked* about something destructive got
flagged:

```
2.5.6:  """Backup tool. Never run rm -rf / by hand."""   -> FLAGGED
2.5.7:  same file                                        -> clean
```

Worse, the self-test didn't ask — it **refused outright**, with no way through,
and the polish loop stopped dead on it. For an entire legitimate category of tool
(disk formatter, bulk deleter, secure wipe) the self-test feature simply did not
exist.

- Comments and docstrings are blanked before scanning. Ordinary string literals
  are **not** — `os.system("rm -rf /")` hides the command inside a string, so
  blanking strings wholesale would have switched the check off completely. A
  non-docstring help string can still trip it, and that's the right way round to
  be wrong: a false positive costs one click, a false negative costs the machine.
- `dd if=` narrowed to `dd … of=/dev/`. `dd if=/dev/sda of=backup.img` is a
  **read** — completely ordinary for a disk-imaging tool, and it was being
  flagged.
- `os.fork()` dropped. Forking is how a tool daemonises; the fork-bomb pattern
  catches actual bombs.
- **The self-test now asks instead of refusing.** A ⚠ *run it anyway* button
  appears and the self-test proceeds on the hidden display. ▶ launch has always
  worked this way; the self-test now matches it.

Verified: real `os.system("rm -rf /")`, `subprocess.run("rm -rf ~", shell=True)`,
`mkfs`, `dd of=/dev/sda` and `rmtree("/etc")` are all still caught.

### The model declining — routed around, and reported honestly

A model that declines returns prose with no code. TheDawg treated that as a
finished turn: no code found → the follow-up helper spent **another** model call
turning "I can't help with that" into tappable multiple-choice options. You got a
fake intake questionnaire instead of either a tool or a straight answer.

Now a decline is detected (narrowly — only at the start of a short, code-free
reply, so a tool that *prints* "Sorry, file not found" isn't mistaken for one),
and the build moves to the next model in your chain. Models differ a lot on this
and the next one often just builds it. If they all decline you get the model's
own words, plainly, with no ⚠ and no invented questions.

**Being straight with you about the limit:** I fixed everything on TheDawg's
side, and routing to another model is a real improvement. But when SiliconFlow's
model itself declines, that's their model's judgement — I'm not going to build
something into your app to defeat it, and anything that claimed to would just be
fragile. Switching provider in Settings is the honest lever; they answer very
differently.

---

## Verified

`thedawg.py`, `clidawg.py`, `shell.py` compile; `ui/index.html` JS syntax-checked;
`--doctor` clean. Regression: the 60s streamed build still delivers code end to
end with live progress; the 2.5.6 fixes (comma imports, CLI doctrine, library
collisions, FULL_REWRITE) all still pass.

## Not changed

No feature or API changes beyond `/api/probe` accepting `confirm`.
`reasoning_effort` still defaults to `high`.
