# TheDawg 2.5.1 — bugfix

No feature, UI, or API changes. Four fixes; the first is the freeze.

## 1. The freeze: builds hung forever after finishing  (the big one)
`/api/chat/stream` — the default path every build takes — sent its events as an
HTTP/1.1 `text/event-stream` with **no `Content-Length`, no chunked encoding, and
no `Connection: close`**. That is unframed HTTP/1.1: the browser's stream reader
never receives an end-of-response, so once the build actually finished (code built,
checks passed, final `result` frame delivered) `reader.read()` blocked forever.
`converse()` never reached `state.busy = false`, the send button stayed disabled,
and the activity spinner ran until a reload.

Reproduced with curl: all event frames arrived in ~0.3s but the connection sat open
for the full request timeout. Fix: the stream response now sets `Connection: close`
and flips `self.close_connection = True`, so the socket closes when the handler
returns and the client gets its EOF. After the fix the same call returns in ~0.35s.
Plain `/api/chat` keeps HTTP/1.1 keep-alive (it has a Content-Length), unchanged.

## 2. Silent stall during provider fallback
The engine emits `stage` events while it works through the model chain
("rate-limited, trying next…", "model unreachable, retrying…"), but the UI's
`onActivity` switch had no `case "stage"` — it dropped them on the floor. A slow
provider fallback therefore looked like a dead build. The UI now shows those lines.

## 3. Deleting an already-gone tool/session leaked a raw errno
`library_delete` / `session_delete` on a missing file returned
`{"error": "[Errno 2] No such file or directory: …"}` into the UI (double-click, or
a stale list). Both are now idempotent: a missing file is success.

## Verified this pass
- all three Python files compile; UI JS parses (node --check)
- every GET and POST endpoint answers fast with no 500s and no hangs
- streamed build now completes and closes end-to-end
- `run_code` still peek-then-detaches GUI launches (no blocking on the window)
- `call_model` provider fallback + timeouts intact
- autotest/smoke still passes valid code and flags unguarded top-level GUI code
- `install.sh` generation still hardened (shell_safe / pip_safe / quoted array)
- UI has no missing-function references; intake/follow-up/github parse defensively
