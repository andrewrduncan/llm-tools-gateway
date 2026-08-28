---
title: Bandwidth-bound decode
type: concept
tags: [performance, quantisation, context]
updated: 2026-08-28
sources: [analyses/engine-choice.md]
verified_at: 2026-08-28
---

# Bandwidth-bound decode

On consumer and integrated GPUs, token generation is limited by **memory
bandwidth**, not compute. The GPU waits on memory.

```
tok/s ≈ effective bandwidth ÷ bytes read per token
bytes/token = (active params × bytes per param) + KV cache
```

## What follows

**Total parameters barely matter — active parameters do.** A sparse MoE reading 3B
active beats a dense 7B, despite being far larger.

**Quantisation is a linear win.** 4-bit reads a quarter of bf16. This is usually the
single largest lever — *if* the engine can actually run it.

**Context is a tax on every token.** KV grows linearly and is re-read per token.
Measured on one machine: 16.2 tok/s at fresh context, 4.5 tok/s at 48K — a 3.6×
penalty from session length alone.

**KV cost is architectural, not size-based.** It scales with
`layers × kv_heads × head_dim`. Sliding-window attention can make a model far
cheaper at depth: one model measured **34 KB/token** where the naive formula
predicted 240 KB.

**Prefix caching makes fixed prompt overhead nearly free** — but only while the
prefix is stable. Any per-request tool selection breaks it.

## Practical order of levers

1. shorter sessions (free, largest)
2. quantisation (if supported)
3. fewer active params (costs quality)
4. cheaper KV architecture
5. speculative decoding
