---
title: Engine choice — vLLM vs llama.cpp
type: analysis
tags: [performance, decision, quantisation]
updated: 2026-08-28
sources: [sources/llamacpp-vulkan-strix-halo.md, "measured on AMD Ryzen AI Max+ 395 (gfx1151)"]
verified_at: 2026-08-28
---

# Engine choice: vLLM vs llama.cpp

Measured on one machine, same model (Qwen3-Coder-30B-A3B), same prompt:

| engine | backend | quant | tok/s | load |
|---|---|---|---|---|
| vLLM | ROCm | bf16 *(forced)* | **16.2** | 105-350 s |
| llama.cpp | Vulkan | Q6_K | **69-77** | **15 s** |

**4.3×**, plus a 514 MB image against 73 GB.

## Why the gap

**Quantisation was unavailable in one engine.** On this GPU vLLM reported
`supports_fp8 = False`, and its int4 path fell back to generic kernels because the
fast ones are CUDA-only. That forced bf16 — 6.0 GB read per token. llama.cpp's GGUF
kernels are native, and Q6_K reads 2.5 GB.

**The "wrong" backend was faster.** Vulkan/RADV outran ROCm/HIP on the same AMD
chip (~80 vs ~59 tok/s decode). Vendor-native is not automatically fastest.

**Memory visibility differed.** Vulkan exposed 111 GiB (VRAM + GTT); the other
engine saw only the 96 GiB BIOS carve-out.

## The transferable lesson

vLLM's strengths — continuous batching, high-concurrency throughput — are worth
nothing to a single user. They were selected for, and paid for, without benefit.

More importantly: **when quantisation turned out to be unsupported, that was
treated as a constraint to live with rather than as a signal that the engine was
wrong for the hardware.** The single biggest performance lever being unavailable
should prompt re-examining the engine, not accepting a 4× penalty.

## What did NOT change

Tool calling. llama.cpp parses tool calls from the model's Jinja chat template;
vLLM used dedicated per-model parsers. Both produced valid structured calls, and
the gateway needed no modification — see
[concepts/endpoint-side-tools](../concepts/endpoint-side-tools.md).
