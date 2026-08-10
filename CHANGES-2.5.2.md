# TheDawg 2.5.2 — speed + robustness pass

Follows 2.5.1 (which fixed the build-stream freeze). This round: measured where
the time actually goes, cut wasted model round-trips, and put the two biggest
latency knobs in your hands. No behaviour change by default.

## What I measured first (so this isn't guesswork)
- Server-side per-request latency: **sub-millisecond** (median ~0.7ms across
  /api/status, /library, /sessions, /, under 50-hit bursts).
- Build overhead with the model stubbed to zero latency: **~36ms**, all of it the
  smoke-test subprocess + ruff, and fully cached on repeat.
- Conclusion: the app isn't slow — the wall-clock is the model round-trips. So the
  fixes target those, not the plumbing.

## 1. Fewer model round-trips on fix rounds
The silent autotest fix loop patched failures through the targeted-edit path with
`retries=1` — so a patch that came back present-but-non-matching burned a SECOND
full high-effort model call to correct the patch format before falling back to a
full rewrite. On the remedial path, where you're already waiting, that retry is
pure latency: we go straight to the rewrite now (`retries=0`). Verified: a
non-matching fix patch drops from 2 model calls to 1. Healthy builds are unchanged
(1 call); edit-mode token savings on the main change path are untouched.

## 2. Two speed levers, no source editing
Both default to today's behaviour — set them only if you want to trade a little
build quality for speed:

- `THEDAWG_REASONING=medium`  (also: low / high / none)
  The build path runs DeepSeek V4 at `reasoning_effort: high`, which is the single
  biggest chunk of build wall-clock (large hidden reasoning budget before it writes
  a line). `medium` is typically ~2x faster to first token and still writes solid
  single-file tools. Default stays `high` so nobody's quality changes unless they
  ask. Unsupported gateways ignore the field safely.

- `THEDAWG_AUTOTEST_ROUNDS=1`  (0–5, default 3)
  Each fix round that fires is another full model round-trip. Capping at 1 bounds a
  struggling build to two calls instead of four.

Set them per-run:  `THEDAWG_REASONING=medium thedawg`
or persist them in your shell rc / the .desktop launcher.

## Robustness (the "run it again and again" part)
- 25 fresh builds in a loop: all pass, 1 model call each, heap grows <0.5MB (bounded
  cache fill, no leak), overhead median 0.6ms.
- 10 consecutive streamed builds through the live server: every one closes in ~0.31s,
  zero timing drift, server stays healthy, no leaked GUI processes.
- All three Python files compile; UI JS parses; every GET/POST endpoint answers with
  no 500s and no hangs; env levers parse and clamp (garbage → safe default).
