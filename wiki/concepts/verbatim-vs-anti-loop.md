---
title: Anti-loop sampling vs verbatim quotation
type: concept
tags: [sampling, tools, correctness]
updated: 2026-10-07
sources: [gateway/server.py]
verified_at: 2026-10-07
supersedes: part of [[engine-parameter-dialects]]
---

# Anti-loop sampling vs verbatim quotation

DRY penalises repeated multi-token **sequences**. Copying
`Tuesday, October 06, 2026` out of a `get_current_datetime` result *is* a
repeated sequence. DRY cannot tell faithful quotation from a degenerate loop, so
it suppresses the correct tokens and the model substitutes plausible ones from
its training prior.

Measured on Qwen3-VL-30B, asked to restate a date the tool had just supplied:

| Sampling | Correct |
|---|---|
| none | 3 / 3 |
| `repetition_penalty 1.1` | 3 / 4 |
| DRY, `dry_penalty_last_n 1024` | **0 / 4** |
| DRY, `dry_penalty_last_n 128` | **0 / 3** |
| DRY, `dry_penalty_last_n 64` | **0 / 3** |

Wrong answers clustered on `October 5, 2023` — the training prior, not noise.

**Shrinking the look-back does not help.** The quoted text sits only tens of
tokens back, so any window wide enough to catch a real loop also covers the
thing you want reproduced. 64 is llama.cpp's own default and still fails every
time.

This was self-defeating for a tool gateway, whose whole purpose is feeding the
model information it could not otherwise have — and then garbling it on the way
out. A user asking "what's today's date" got **October 06, 2178**.

## Resolution

Anti-loop sampling is skipped when the incoming request already carries tool
results, and dropped the moment the gateway records an assistant turn with
`tool_calls` (`drop_anti_loop()`). Loop protection still applies to ordinary
generation, which is where runaway repetition actually occurs.

## Supersedes

[[engine-parameter-dialects]] records fixing an inert anti-repetition guard by
sending both parameter spellings plus DRY. That remains correct for plain
generation. It was wrong for any turn containing tool output, which is most
turns through this gateway.

Likely the same root cause as the mangled image URLs described in
[[image-lifecycle]]: a model prevented from reproducing a string exactly. The
`repair_image_urls()` workaround may now be doing less work than it was built
for.
