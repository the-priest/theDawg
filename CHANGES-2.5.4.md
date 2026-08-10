# TheDawg 2.5.4 — more bugs, found by reading

Another read-through pass (model path, analyzer, GitHub flow, UI). Three real
bugs, none of which throw on a happy path — they bite under specific conditions,
which is why they were still here.

## 1. GitHub publish used the wrong code extractor (truncation risk)
The publish flow called `splitReply(rel.reply)` WITHOUT the second argument — so it
threw away the server's authoritative extracted code (`rel.code`) and re-parsed the
markdown with the simpler client-side matcher. That matcher closes a ``` fence at
the first ``` line inside it, so a tool whose own --help prints a fenced example
gets truncated — and the truncated version is what goes into the RELEASE build and
the published GitHub repo. This is the exact "two extractors disagree" bug that was
fixed on the normal build path; the publish path still had it. Now passes
`rel.code`.

## 2. _http_post poisoned the process-wide socket timeout from worker threads
`_http_post` did `socket.setdefaulttimeout(45)` then restored it in a finally. That
setting is PROCESS-GLOBAL, but model calls run on concurrent threads (a polish
round overlapping a build; the parallel model-fetch threads at startup). Two threads
racing on that global can leave the default timeout stuck on every OTHER socket in
the process — the local HTTP server, the Xvfb connection waits. And it was
redundant: `urlopen(req, timeout=45)` already bounds connect+read for the request.
Removed the global mutation entirely; the per-request timeout stays.

## 3. Published .desktop launcher used an invalid field code
The .desktop file written into each GitHub repo had
`Exec=python3 %h/.local/share/<repo>/<tool>.py`. `%h` is NOT a Desktop Entry field
code (the spec defines %f %F %u %U %i %c %k and a handful more), so it never
expanded to $HOME — the menu entry silently failed to launch for anyone who copied
the shipped file by hand. (install.sh generates its own correct entry at install
time with an absolute path, so curl|bash installs were fine — this only hit the
committed file.) Now `Exec=<tool>`, the CLI launcher install.sh puts on PATH, which
is valid and portable.

## Read and confirmed NOT broken (so nobody re-checks them)
- fetch_models host discovery / model filtering — sound.
- probe_run process/Xvfb/display teardown — reaps cleanly, releases the display.
- looks_dangerous and _extra_safety_findings (except:pass, shell=True) — precise,
  no false positives on correct code.
- the syntax highlighter regex — no zero-width match, no catastrophic backtracking.
- usage/cost accounting, semver bump, autosave, init() double-boot guard — correct.

Cumulative with 2.5.1–2.5.3.
