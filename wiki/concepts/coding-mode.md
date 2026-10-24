---
title: Coding mode
type: concept
tags: [gpu-memory, models, coding, orchestration]
updated: 2026-10-24
sources: [gateway/coding.py, gateway/server.py, gateway/config.py, measurements 2026-10-24 on gfx1151]
verified_at: 2026-10-24
---

# Coding mode

Two model configurations, each of which fits the GPU, whose union does not —
and the gateway owning the switch between them.

```
chat    strong chat model                       image + video tools offered
coding  coder + small chat model beside it      image + video tools withheld
```

## Why the gateway, and why ordering

The obvious design — point the coding client straight at the coder's model
server — fails on memory. Nothing would unload the chat model first, so the
coder's load would land beside it. On this stack an overcommitted GPU does not
fail cleanly: the driver tears down a device context and llama.cpp's vision
encoder starts failing *inside 200 responses* (see the device-loss history in
the deployment repo, commit 4370b98).

So the coder is reachable only through `/coder/v1` on the gateway. That door is
a raw passthrough — no tools merged, no sampling changes, no history rewriting,
because an agentic client brings its own tools and needs verbatim output (see
[verbatim-vs-anti-loop](verbatim-vs-anti-loop.md)) — and its one addition is
ordering: a POST enters coding mode and is not forwarded until the chat router
reports the big model **unloaded**. Asking is not enough; the memory is free
only when the router says so (`_unload_all()` polls).

The deployment publishes no port for the coder's server. That is the structural
half of the guarantee: the door is the only way in.

## The rules while coding

- **Every chat-side request is rewritten to the small model** — including the
  client's hidden bookkeeping calls (title, tags) and catch-all passthrough
  POSTs. One request naming the big model would autoload it beside the coder.
- **GPU-heavy tools are withheld, not discouraged** (`CODING_DISABLED_TOOLS`).
  A render's peak plus the coder overcommits.
- **Replies carry a one-line note**, so a weaker answer or a missing image tool
  is never a mystery.
- **Entry refuses (503, with the reason) while ComfyUI has a job queued.**

## Leaving

After `CODING_IDLE_MINUTES` with no coder traffic, never while a coder request
is in flight; or on `POST /admin/mode {"mode": "chat"}`. The coder is unloaded
*before* the mode flips back, and `CHAT_PRELOAD_MODEL` is loaded in the
background so the first chat afterwards does not wait.

## Restart safety

The gateway restarts on every image update. A background loop
(`ModeManager._reconcile`) treats a resident coder as proof of coding mode, so a
restart mid-session re-adopts it — and evicts the big chat model if it somehow
came back — instead of believing it is in chat mode with the GPU committed.

## Measured (2026-10-24, gfx1151, Vulkan llama.cpp)

| state | GPU in use |
|---|---|
| chat: 30B-A3B Q6_K, 131k ctx over 2 slots | 40.1 GB |
| coding: coder 80B-A3B Q5_K_XL at 262k + 8B Q6_K at 32k | 79.6 GB |

Switch into coding, including the coder's load: 26 s. Back to chat: 18 s.

The coder's full 262k context is cheap because only 12 of its 48 layers keep a
per-token KV cache; the coder alone measured 66.8 GB resident against a
~67 GB estimate from weights + architecture.

Coder throughput by context depth (cold prompt, 128-token answer):

| depth | prefill tok/s | gen tok/s |
|---|---|---|
| 2,229 | 412 | 43.7 |
| 64,584 | 346 | 32.4 |
| 117,611 | 276 | 27.0 |
| 190,013 | 194 | 22.3 |

**Prefix caching works for this model**, unlike the vision models (see
[context-cost-of-images](context-cost-of-images.md)): with 190k tokens in
context, a follow-up turn processed 19 new tokens in 1.5 s. llama.cpp restores
a context checkpoint for the model's recurrent layers. The cold 16-minute
prefill is a once-per-session cost, not a per-turn one.

## Configuration

`CODER_URL`, `CODER_MODEL`, `CODING_CHAT_MODEL`, `CHAT_PRELOAD_MODEL`,
`CODING_IDLE_MINUTES`, `CODING_DISABLED_TOOLS` in `gateway/config.py`. Empty
`CODER_URL` (or model names) turns the feature off entirely.
