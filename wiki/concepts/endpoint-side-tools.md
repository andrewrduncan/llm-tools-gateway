---
title: Endpoint-side tools
type: concept
tags: [architecture, tools]
updated: 2026-08-28
sources: [README.md, gateway/server.py]
verified_at: 2026-08-28
---

# Endpoint-side tools

In the OpenAI chat API, `tools` are supplied by the **client, per request**. The
model server only parses tool calls out of generated text; it never owns or
executes tools.

That places the same burden on every client, and excludes clients that cannot
host tools at all.

A gateway that injects and executes its own tools inverts this: capability
becomes a property of the **endpoint**, not the caller.

## Consequences

- add a tool once, every client gains it with no configuration
- swap the model or the entire inference engine — tools are unaffected
- `curl` gets the same abilities as a full agent

## Cost

Tool definitions are prompt tokens on **every** request, including ones that need
no tools. That is a real floor for minimal clients.

Mitigating instinct — sending one `get_tools` and fetching the rest on demand — is
usually **wrong**: it varies the prompt prefix per request and defeats prefix
caching, which is what makes the overhead nearly free in the first place. See
[bandwidth-bound-decode](bandwidth-bound-decode.md).

Related: [tool-ownership-split](tool-ownership-split.md)
