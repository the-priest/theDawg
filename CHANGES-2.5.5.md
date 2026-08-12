# TheDawg 2.5.5 — the build path actually finishes now

**One bug caused almost everything you described.** Slow, gets stuck, gives you no
code, errors out after a few minutes — all four are the same root cause.

## The bug

`_http_post()` had `timeout=45`, and every model call in the app went through it.

45 seconds is a reasonable bound for a chat reply. It is nowhere near enough for
the build path, which asks a **reasoning model**, at `reasoning_effort=high`, with
`max_tokens=32000`, to write an entire GUI tool — and then waits for the *last*
token before anything comes back. That call routinely takes 1–4 minutes.

So the read timed out. Every time. On a request the provider was happily still
working on and still billing you for.

What happened next made it worse. Because you pin a model in Settings,
`single_pick` is true, so the transient-error branch re-sent the *same* request
twice more:

```
 45s  first attempt   -> timed out
 45s  retry 1         -> timed out
 45s  retry 2         -> timed out
────
137s  "chain failed. Last: retry failed: timed out"
```

And that is one `call_model()`. A normal edit turn does two — the targeted-patch
round, then the full rewrite when the patch round "fails" — so:

**274 seconds → 4.6 minutes → no code.** Which is exactly what you were seeing.

Reproduced against a fake provider that behaves like a healthy one taking 70s:

```
ORIGINAL v2.5.4, healthy provider that takes 70s: failed after 137s
  error: fake-model: retry failed: timed out
```

Same scenario on 2.5.5: **succeeds in 70s with the code delivered.**

## The fix — stream the model response

Rather than just raising the number (which trades one wrong guess for another),
the transport now streams.

- New `_http_post_stream()` reads the provider's SSE frames as they arrive and
  returns the *same shape* a normal call returns, so nothing downstream changed.
- The meaningful timeout is now **idle time, not total time**: 90s without a
  single byte means dead; a model that is slowly writing keeps its connection.
  A genuinely dead endpoint still fails fast.
- Total ceiling is per tier: 900s build, 120s clerical.
- If a gateway rejects `stream`, that `(provider, model)` is remembered and the
  plain path is used — with a sane budget this time.

Three env levers, all optional:

    THEDAWG_IDLE_TIMEOUT=90     # seconds of silence before giving up
    THEDAWG_TIMEOUT=900         # hard ceiling, build calls
    THEDAWG_TIMEOUT_CHEAP=120   # hard ceiling, clerical calls

## Seven more, all on the same path

1. **The retry storm.** A timeout used to trigger two automatic re-sends of the
   same request. That was defensible against a 45s cap; against a 900s one it
   means re-running a generation the provider may have nearly finished — three
   times the wait, three times the bill. Now only *fast* failures retry (dead
   host, connection reset, DNS). A call that burned its budget moves on.

2. **"Doesn't give me any code", cause two.** `finish_reason` was never read
   anywhere in the file. When a reasoning model spent its whole `max_tokens`
   budget thinking and emitted no content, `reply = content or reasoning_content`
   handed the raw internal monologue back *as the answer*. No code in it, so the
   follow-up helper spent **another** model call turning the monologue into
   tappable "questions" — and you got a wall of the model's thinking and no tool.
   Now: `finish_reason=length` with empty content is reported as what it is.

3. **Truncated builds.** A reply cut off mid-file used to go into the auto-fix
   loop as if it were buggy code — including being handed to the SEARCH/REPLACE
   patcher, which cannot anchor against text past the cut. Up to three rounds
   burned on a fragment. Now a truncated build skips the patcher and tells the
   model plainly: you ran out of room, write it smaller.

4. **The wait, doubled.** `try_edit_round()` returned `None` both when the patch
   didn't apply *and* when the model call itself errored. `None` means "fall
   through to a full rewrite" — so a dead provider got the identical failing
   request sent again with a bigger payload. That is the second 137 seconds in
   the 4.6 minutes above. Provider failures now propagate instead.

5. **The SSE watchdog fired on healthy builds.** `_stream_build` declared "the
   build timed out" after 120s of quiet — *below* the new transport budget, so it
   would have killed the client's connection while the worker thread kept
   building, and thrown the finished code away. Now derived from the transport
   budget, with an error message that says what to change.

6. **A wedged build thread.** `ActivityChannel.finish()` used blocking `put()`
   on a 256-slot queue. If the client disconnected mid-build nothing drained it,
   and a chatty build blocked there forever holding its own result. Non-blocking
   now; the backlog is dropped, the result gets through.

7. **The browser silently re-ran whole builds.** `converse()` falls back to
   `/api/chat` when the stream fails — right, when streaming isn't *supported*;
   wrong after a build has been running, where it means a second full generation
   at double the cost. Now only retries if nothing had started.

## And you can see it working

The streamed tokens feed the activity channel, so the build status shows a live
word count and elapsed seconds while the model writes:

    writing code · 340 words written · 47s

That is the difference between "it's stuck" and "it's busy" — which, for a build
that legitimately takes two minutes, was most of the problem.

## Not changed

`reasoning_effort` still defaults to `high`; that is your call, and
`THEDAWG_REASONING=medium` is there when you want the speed. No UI, API, or file
format changes. `shell.py`, `install.sh`, and the assets are untouched.

## Verified

Against a fake OpenAI-compatible gateway (`ui/index.html` JS syntax-checked,
`thedawg.py` and `clidawg.py` compiled, `--doctor` clean):

| scenario | before | after |
|---|---|---|
| healthy provider, 70s generation | fails at 137s, no code | 70s, code delivered |
| 60s build, full SSE relay to client | — | code delivered, 8 live progress frames |
| gateway that rejects `stream` | — | falls back, 1.0s, code delivered |
| reasoning eats the whole budget | monologue + fake questions | clear error |
| hung endpoint | 137s (3 × 45s) | 4s on the idle guard |
| build truncated mid-file | patched as if buggy | rewritten smaller, passes |
