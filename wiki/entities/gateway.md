---
title: Gateway
type: entity
tags: [component]
updated: 2026-08-28
sources: [gateway/server.py, gateway/config.py]
verified_at: 2026-08-28
---

# Gateway

The proxy itself. Responsibilities, in order of a request:

1. **Identify the caller** — forwarded user header, else API key, else anonymous
2. **Merge tools** — client tools first, gateway tools appended; client wins on
   name collisions
3. **Apply sampling floors** — repetition penalty and a minimum temperature, only
   when the client set none
4. **Loop** — call upstream; if the model called gateway tools, execute and repeat;
   if it called client tools, hand the turn back
5. **Stream** — forward tokens live, emitting tool progress on `reasoning_content`

## Endpoints

| Path | Purpose |
|---|---|
| `POST /v1/chat/completions` | the tool loop |
| `GET /health` | status and the tools currently live |
| `GET /img/{key}` | re-serve stored images with credentials |
| `POST /admin/index` | ingest a document for RAG (operator, not a model tool) |
| `*` | transparent passthrough to the model server |

## Notes

`/img/` exists because some S3 implementations refuse anonymous reads, which makes
raw object URLs render as broken images in a browser. The gateway holds the
credentials and re-serves.

Bookkeeping calls from chat UIs (title/tag generation) are detected and proxied
**without** tools, so the model does not waste real tool calls on them.
