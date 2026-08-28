---
title: Subject partitioning
type: concept
tags: [memory, multi-user, security]
updated: 2026-08-28
sources: [gateway/memory.py, schema.sql]
verified_at: 2026-08-28
---

# Subject partitioning

One rule governs every memory, document and file row:

```sql
-- write
subject = <caller identity>   -- or NULL when explicitly shared
-- read
WHERE subject = :caller OR subject IS NULL
```

Personal entries stay personal; `NULL` is shared with everyone.

## Identity: two mechanisms, deliberately separate

| Caller | Mechanism |
|---|---|
| Multi-user UI | forwards a user header per request — one API key, N humans |
| CLI / script | one API key per machine, mapped to a subject |

Keys identify **machines**; forwarded headers identify **humans**. Adding a person
to the UI needs no gateway change.

## Traps

**Header trust.** Forwarded identity headers are trusted blindly unless signed. On
an open network that is impersonation. Signed JWT variants exist in some UIs and
should be preferred.

**Key storage.** Keys live in a file, not the database, so authentication does not
depend on the database being reachable.

**Tool descriptions matter more than the code.** A `recall` tool that works
perfectly is useless if the model never calls it. Phrasing the description
tentatively caused it to be skipped entirely; making it directive fixed retrieval
with no logic change.
