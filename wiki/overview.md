---
title: Overview
type: analysis
tags: [architecture, entrypoint]
updated: 2026-08-28
sources: [README.md, gateway/server.py, gateway/config.py]
verified_at: 2026-08-28
---

# Overview

`llm-tools-gateway` is an OpenAI-compatible proxy that gives **any** local LLM a
fixed set of server-side tools, so no client needs configuring.

## The problem it solves

In the OpenAI API, tools are **client-supplied per request**. Consequences:

- every client re-implements the same tools
- clients that cannot host tools (plain `curl`, minimal SDKs) never get them
- two clients hitting one model disagree about what the model can do

Moving tools to the endpoint fixes all three at once. See
[concepts/endpoint-side-tools](concepts/endpoint-side-tools.md).

## Shape

```
client ──► gateway (:8000) ──► model server (:8001)
              │
              ├─ executes ITS OWN tools and loops
              └─ passes CLIENT tools straight back
```

That split is the load-bearing design decision. See
[concepts/tool-ownership-split](concepts/tool-ownership-split.md).

## Layers

| Layer | Page |
|---|---|
| Proxy + tool loop | [entities/gateway](entities/gateway.md) |
| Model server (interchangeable) | [entities/model-backend](entities/model-backend.md) |
| Memory / RAG | [concepts/subject-partitioning](concepts/subject-partitioning.md) |
| Image generation | [entities/comfyui-workflows](entities/comfyui-workflows.md) |

## Non-obvious findings

- [analyses/engine-choice](analyses/engine-choice.md) — quantisation support can
  matter more than the inference engine's headline features
- [concepts/bandwidth-bound-decode](concepts/bandwidth-bound-decode.md) — what
  actually determines tokens/sec locally
