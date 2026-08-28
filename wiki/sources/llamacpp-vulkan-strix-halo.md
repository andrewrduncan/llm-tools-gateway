---
title: llama.cpp on Strix Halo (gfx1151) — benchmark notes
type: source
tags: [benchmark, amd, vulkan]
updated: 2026-08-28
sources: ["https://strixhalo.wiki/AI/llamacpp-performance", "https://github.com/ggml-org/llama.cpp/discussions/20856"]
verified_at: 2026-08-28
---

# Source: llama.cpp on Strix Halo

Community benchmarks for AMD Ryzen AI Max+ 395 / Radeon 8060S (gfx1151), up to
128 GB unified LPDDR5X.

## Claims taken from the source

- Vulkan/RADV decodes **faster than ROCm/HIP** on this GPU: ~80 vs ~59 tok/s; a
  backend swap alone moved 47.7 → 60.9 tok/s (+28%).
- Qwen3-30B-A3B measured at **98.32 tok/s** (pp512/tg128).
- Guidance: use llama.cpp for generation-heavy single-stream GGUF work; use vLLM
  for prompt-processing-heavy, high-concurrency, batched cases.

## Independently reproduced here

Qwen3-Coder-30B-A3B Q6_K via llama.cpp/Vulkan: **69.2 tok/s direct, 77.1 through
the gateway**, versus 16.2 for the same model at bf16 under vLLM/ROCm.

Lower than the source's 98 tok/s, consistent with a heavier quant (Q6_K vs
whatever the source used) and a different model variant.

## Build notes

- Vulkan backend needs `SPIRV-Headers` + `spirv-tools` at compile time.
- llama.cpp builds shared libraries; a runtime image must copy the whole `bin/`
  directory or the server fails with `libllama-server-impl.so: cannot open shared
  object file`.
