---
title: Index
type: index
updated: 2026-08-31
---

# Index

## Start here
- [overview](overview.md) — what this is and why it exists
- [conventions](conventions.md) — how this project is worked on

## Entities
- [gateway](entities/gateway.md) — the proxy, tool loop, identity, progress
- [model-backend](entities/model-backend.md) — interchangeable OpenAI-compatible server
- [comfyui-workflows](entities/comfyui-workflows.md) — user-supplied image graphs

## Concepts
- [endpoint-side-tools](concepts/endpoint-side-tools.md) — why tools live at the endpoint
- [tool-ownership-split](concepts/tool-ownership-split.md) — whose tool is it
- [subject-partitioning](concepts/subject-partitioning.md) — multi-user memory
- [bandwidth-bound-decode](concepts/bandwidth-bound-decode.md) — what sets local tok/s
- [image-lifecycle](concepts/image-lifecycle.md) — three copies, opaque keys, unauthenticated serving
- [engine-parameter-dialects](concepts/engine-parameter-dialects.md) — silently dropped sampling params

## Analyses
- [engine-choice](analyses/engine-choice.md) — vLLM vs llama.cpp, with measurements

## Sources
- [llamacpp-vulkan-strix-halo](sources/llamacpp-vulkan-strix-halo.md) — benchmark data
