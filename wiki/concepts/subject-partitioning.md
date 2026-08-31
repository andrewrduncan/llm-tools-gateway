---
title: Subject partitioning
type: concept
tags: [memory, multi-user, security]
updated: 2026-08-30
sources: [gateway/memory.py, gateway/tools.py, schema.sql]
verified_at: 2026-08-30
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

## Reading and deleting need different rules

The read rule above is **wrong for destructive operations**. `OR subject IS NULL`
makes shared rows visible to everyone, which is the point — but reused for a
delete it makes shared rows *deletable* by everyone:

```sql
-- read  : subject matches OR the row is shared
AND (subject IS NOT DISTINCT FROM :caller OR subject IS NULL)
-- write : subject must match exactly
AND subject IS NOT DISTINCT FROM :caller
```

`find_file(ref, subject, for_write=False)` takes a flag for exactly this reason
(`gateway/memory.py`). Shared images stay viewable by anyone and deletable only by
whoever created them.

This was a real defect, not a hypothetical: `delete_image` initially reused the
read predicate, and a caller identifying as a different subject successfully
deleted a shared image. Any partitioned store that grows a delete path inherits
this trap — the read rule is the natural thing to reach for and it is the wrong one.

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
