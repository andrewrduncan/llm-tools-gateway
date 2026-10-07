---
title: The context cost of images
type: concept
tags: [vision, context, performance]
updated: 2026-10-07
sources: [gateway/server.py, gateway/config.py]
verified_at: 2026-10-07
---

# The context cost of images

A vision model turns one screenshot into tens of thousands of tokens, and the
whole conversation is re-sent on every turn. On llama.cpp this compounds: it
logs `cache_reuse is not supported by multimodal, it will be disabled` at
startup, so **prefix caching is off entirely for vision models** and every
historical image is re-encoded from scratch on every request.

## Measured

The first image generation in a chat behaves normally. The second turn — an edit
carrying the generated image plus a newly uploaded screenshot — produced:

```
prompt processing, n_tokens = 47104, progress = 0.45
```

A ~105,000-token prompt advancing roughly 2,048 tokens per 15 seconds: about
**13 minutes of prefill** before the model emitted anything. It presents as a
hang and is really just arithmetic.

## Resolution

`strip_history_images()` forwards only the newest `KEEP_RECENT_IMAGES` images
and replaces older ones with
`[earlier image removed from history to save context]`. ~15 tokens instead of
~15,000.

The placeholder matters. Deleting silently leaves the model unable to explain
its own history — it loses that an image existed at all, and a follow-up like
"make it darker" becomes incoherent. With the placeholder it knows one was
there; it simply cannot re-examine pixels it already described.

The newest image survives, which is the one a user just attached or the model
just produced — the only one a follow-up normally refers to.

## Consequences elsewhere

This is what makes inline private images affordable: an inline image is ~150 KB
of base64 that would otherwise be re-sent and re-encoded every turn. With
stripping it costs one turn. See [[private-mode]].

Raise `KEEP_RECENT_IMAGES` if comparisons across turns matter more than latency.
Each additional retained image costs its full re-encode on every subsequent
request.
