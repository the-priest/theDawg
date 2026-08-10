# TheDawg 2.5.3 — bugs found by reading the code

No test harnesses this round — read the model path, the analyzer, and the UI
line by line. Three real bugs, all in code that only bites under specific real
conditions (which is why they survived the earlier passes).

## 1. A rate-limit (429) dead-ended the build instead of falling back
In call_model, a 429 from the primary provider did `return {"error": ...}` —
which exits the function BEFORE `_try_fallback()` runs. So despite
`FALLBACK_PROVIDERS = ["groq"]` existing for exactly this case, a SiliconFlow
quota/rate-limit hard-failed the build instead of trying Groq. The giveaway was
the code's own emit line, "…trying next…", right above a return that never tried
next. Now it breaks out of the provider's model chain and falls through to the
fallback provider; if there's no keyed fallback, the surfaced error still says it
was rate-limited. This is the one you'd hit on a free-tier key.

## 2. The 401 regional-host retry could return an empty build
SiliconFlow has .com/.cn hosts; a 401 can just mean the wrong regional host, so
call_model rediscovers the host and retries once. That retry path unpacked the
response as `data["choices"][0]["message"]["content"]` — the only non-defensive
unpack left in the function. On a null `content` it returned `{"reply": None}`,
which downstream became a silent empty build (no code, no error). Now it uses the
same guarded unpack as every other path and skips an empty reply.

## 3. The static analyzer false-flagged class-level attributes
`_unassigned_self_attrs` catches "self.x read but never assigned" (a real
click-time AttributeError the import check can't see). But it only counted
attributes assigned via `self.x = …` or method names — NOT class-level ones:

    class Counter:
        count = 0                 # class attribute
        def bump(self):
            return self.count+1   # was flagged: "self.count never assigned"

So a completely correct, common pattern got reported as a bug, and the autotest
loop then made the model rewrite good code — the worst outcome for an analyzer,
and a wasted (slow) fix round. It now counts class-body `name = …`,
`name: T = …`, tuple/list class assignments, and bare annotations as defined.

## Notes from the read that are NOT bugs (so they don't get "fixed" later)
- The signature/arity checks are precision-first: they intentionally MISS some
  cases (kw-only over-calls, variadics) rather than risk a false positive. Left.
- The syntax highlighter's regex can't zero-width-match (no infinite loop) and its
  inner alternations are unambiguous (no catastrophic backtracking). Left.
- trim_history can still split a turn-pair (assistant reply from its user turn) —
  known, needs a restructure not a patch, unchanged.

Cumulative with 2.5.1 (stream-freeze fix) and 2.5.2 (speed levers + one fewer
fix-round round-trip).
