---
title: Model backend
type: entity
tags: [component, interchangeable]
updated: 2026-08-28
sources: [gateway/config.py, README.md]
verified_at: 2026-08-28
---

# Model backend

Any server implementing `POST /v1/chat/completions`. Set by `UPSTREAM_URL`; the
gateway holds no other assumption about it.

Verified working: **vLLM**, **llama.cpp** (`llama-server`). Expected to work:
Ollama, LM Studio, TGI, or a hosted API.

## Tool-call parsing differs, and does not matter

| Server | Mechanism |
|---|---|
| vLLM | dedicated per-model parsers (`--tool-call-parser`) |
| llama.cpp | the model's Jinja chat template (`--jinja`) |

Both emit standard structured `tool_calls`, so the gateway is unaffected. If a
backend ever returned tool calls as plain text, the gateway is the natural place to
parse them — it already sees raw output.

## Selection guidance

See [analyses/engine-choice](../analyses/engine-choice.md). Short version: for
single-user local work, prefer whichever engine can actually **quantise** on your
hardware — that outweighs most other differences.
