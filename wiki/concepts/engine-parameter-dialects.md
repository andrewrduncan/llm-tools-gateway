---
title: Engine parameter dialects
type: concept
tags: [sampling, portability, silent-failure]
updated: 2026-08-31
sources: [gateway/server.py, gateway/config.py]
verified_at: 2026-08-31
---

# Engine parameter dialects

OpenAI-compatible servers agree on the envelope and disagree on sampling controls.
They **ignore unknown keys silently** — no error, no warning, no log line. A gateway
that speaks one dialect appears to work everywhere and actually works in one place.

Measured against llama.cpp's `/completion`, which echoes back the settings it applied:

```
sent repeat_penalty: 1.1       ->  repeat_penalty = 1.1     applied
sent repetition_penalty: 1.1   ->  repeat_penalty = 1.0     dropped
```

| Control | vLLM / TGI | llama.cpp |
|---|---|---|
| repetition | `repetition_penalty` | `repeat_penalty` |
| look-back window | n/a | `repeat_last_n` (default **64**) |
| sequence repetition | n/a | `dry_multiplier`, `dry_penalty_last_n` |

This caused a real regression. The anti-repetition guard was written against vLLM,
the engine was later swapped to llama.cpp, and the parameter stopped applying — with
no failure signal. Repetition loops returned and were misread as a model problem for
some time. See [[bandwidth-bound-decode]] for the engine migration itself.

## Rules this implies

**Send every dialect.** Each engine ignores the spellings it does not know, so
emitting both names is free and portable.

**Verify against the engine, not the code.** The only proof a parameter applied is
the engine reporting it back. Reading the gateway source proves what was *sent*.

**Re-verify after any engine change.** A setting that was correct when written is not
correct afterwards; nothing surfaces the difference.

**Match the penalty to the failure.** `repeat_penalty` discourages repeated *tokens*;
real loops repeat multi-token *sequences* (a line or block cycling). DRY targets
sequences, and llama.cpp's 64-token look-back is too narrow to see a repeating block.

---

> **Partly superseded (2026-10-07).** Adding DRY here fixed repetition in plain
> generation and **broke verbatim quotation of tool results** — 0/4 correct when
> restating a date the tool had just supplied. Anti-loop sampling is now dropped
> for any turn carrying tool output. See [[verbatim-vs-anti-loop]].
