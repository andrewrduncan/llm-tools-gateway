---
title: Tool ownership split
type: concept
tags: [architecture, tools, compatibility]
updated: 2026-08-28
sources: [gateway/server.py]
verified_at: 2026-08-28
---

# Tool ownership split

The gateway merges its own tools with whatever the client sent, then applies one
rule when the model emits tool calls:

```
all calls are OURS      -> execute them, loop, return the finished answer
any call is the CLIENT's -> return the whole turn untouched, let the client run it
```

## Why this is load-bearing

Without it, a gateway in front of a coding agent **breaks that agent**. The agent
sends its own file-editing tools; the model calls one; the gateway swallows it and
never returns a result. The agent waits forever — a hang, not an error.

With the split, both tool sets coexist in a single turn: the model can call a
gateway tool and a client tool in the same exchange.

## Security

Tools that take a caller identity must have `subject` **injected server-side** and
stripped from anything the model supplied. If the model could set it, it could read
another user's memories. See [subject-partitioning](subject-partitioning.md).
